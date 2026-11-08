"""SharedSignalLayer (#19): señales, vetos y SimulationGate una vez para todos los bots."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from tm_agents.base import AgentContext, BaseAgent
from tm_agents.news.models import Impact, Veto
from tm_agents.news.veto import InMemoryVetoStore
from tm_agents.orchestrator.bot import BotCommand, CommandType
from tm_agents.orchestrator.events import parse_event
from tm_agents.orchestrator.policy import SleevePolicy, policy_for, sell_levels
from tm_agents.orchestrator.runtime import run_offline
from tm_agents.orchestrator.shared import (
    FrameBarsProvider,
    MarketDataBarsProvider,
    SharedSignalLayer,
    StoreVetoProvider,
    bar_close_instants,
    bar_closed,
    default_agent_factory,
)
from tm_agents.orchestrator.streams import MemorySink
from tm_agents.registry import InMemoryBackend, StrategyRecord, StrategyRegistry
from tm_agents.simulation_gate import GateConfig, SimulationGate
from tm_agents.strategy_stats import StrategyState
from tm_shared.contracts import Direction, Horizon, OrderSide, Signal, Timeframe

START = datetime(2023, 1, 1, tzinfo=UTC)
REPORT = {
    "aggregate": {"volatility": 0.38, "sharpe": 0.98},
    "montecarlo": {"max_drawdown": {"p95": 0.615}},
}


def daily(closes, start=START) -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    idx = pd.date_range(start, periods=len(closes), freq="D", tz="UTC", name="open_time")
    return pd.DataFrame(
        {"open": closes, "high": closes * 1.01, "low": closes * 0.99, "close": closes, "volume": 1},
        index=idx,
    )


def h4(closes, start=START) -> pd.DataFrame:
    df = daily(closes, start)
    return df.set_axis(pd.date_range(start, periods=len(df), freq="4h", tz="UTC", name="open_time"))


class FakeKronos(BaseAgent):
    """Kronos de prueba: cuenta cuántas veces se le pide un pronóstico."""

    name = "kronos"
    timeframe = Timeframe.H4

    def __init__(self) -> None:
        super().__init__()
        self.forecasts: list[tuple[str, datetime]] = []
        self._cache: tuple[str, datetime] | None = None

    def _forecast(self, ctx: AgentContext) -> np.ndarray:
        key = (ctx.symbol, ctx.now)
        if self._cache != key:
            self._cache = key
            self.forecasts.append(key)
        last = float(ctx.bars[self.timeframe]["close"].iloc[-1])
        steps = last * (1 + 0.01 * np.arange(1, 7))
        one = np.stack([steps, steps * 1.01, steps * 0.99, steps, np.ones(6)], axis=1)
        return np.repeat(one[None, :, :], 8, axis=0)

    def trajectories(self, ctx: AgentContext) -> np.ndarray:
        return self._forecast(ctx)

    def analyze(self, ctx: AgentContext) -> Signal:
        paths = self._forecast(ctx)
        last = float(ctx.bars[self.timeframe]["close"].iloc[-1])
        return Signal(
            symbol=ctx.symbol,
            direction=Direction.LONG,
            confidence=1.0,
            horizon=Horizon.SHORT,
            timeframe=self.timeframe,
            source=self.name,
            generated_at=ctx.now,
            metadata={"stop_loss": last * 0.98, "take_profit": last * 1.04, "n": len(paths)},
        )


def registry_with(**states: StrategyState) -> StrategyRegistry:
    registry = StrategyRegistry(InMemoryBackend())
    for name, state in states.items():
        registry.register(name)
        registry.backend.update(name, {"state": state, "lab_report": REPORT})
    return registry


@pytest.fixture
def frames():
    up = 100 * 1.012 ** np.arange(400) * (1 + 0.003 * np.sin(np.arange(400)))
    return {
        ("BTCUSDT", Timeframe.D1): daily(up),
        ("ETHUSDT", Timeframe.D1): daily(up / 20),
        ("BTCUSDT", Timeframe.H4): h4(up),
        ("ETHUSDT", Timeframe.H4): h4(up / 20),
    }


@pytest.fixture
def gate():
    return SimulationGate(GateConfig(n_paths=200, fee_rate=0.001))


def test_publishes_one_event_per_strategy_and_symbol(frames, gate):
    registry = registry_with(trend=StrategyState.ACTIVE, intraday_momentum=StrategyState.RETIRED)
    sink = MemorySink()
    layer = SharedSignalLayer(registry, FrameBarsProvider(frames), sink, gate=gate)
    now = START + timedelta(days=300)
    events = layer.on_bar_close(now)

    assert [(e.strategy, e.symbol) for e in events] == [("trend", "BTCUSDT"), ("trend", "ETHUSDT")]
    assert sink.items == events
    ev = events[0]
    assert ev.id == "trend:BTCUSDT:2023-10-28T00:00:00Z"
    assert ev.signal.direction is Direction.LONG and ev.signal.confidence == 1.0
    assert ev.sim is not None and ev.sim_error is None
    assert ev.hard_stop is False and ev.stop_loss < ev.price < ev.take_profit
    assert ev.price == Decimal(str(float(frames[("BTCUSDT", Timeframe.D1)]["close"].iloc[299])))
    assert [s.name for s in ev.catalog] == ["trend"]  # la retirada no está viva
    assert ev.catalog[0].max_drawdown_p95 == pytest.approx(0.615)
    # no hay evento del Trend fuera del cierre diario
    assert layer.on_bar_close(now + timedelta(hours=4)) == []
    # ida y vuelta por el formato del stream
    assert parse_event({"data": ev.to_json()}) == ev
    assert parse_event({b"data": b"{malformado"}) is None


def test_kronos_is_called_once_per_symbol_and_shadow_is_published(frames, gate):
    registry = registry_with(trend=StrategyState.ACTIVE, kronos=StrategyState.SHADOW)
    kronos = FakeKronos()
    sink = MemorySink()
    layer = SharedSignalLayer(
        registry, FrameBarsProvider(frames), sink, gate=gate, agents={"kronos": kronos}
    )
    events = layer.on_bar_close(START + timedelta(days=60))
    by = {(e.strategy, e.symbol): e for e in events}
    assert set(by) == {
        ("trend", "BTCUSDT"),
        ("trend", "ETHUSDT"),
        ("kronos", "BTCUSDT"),
        ("kronos", "ETHUSDT"),
    }
    # señal y trayectorias del gate comparten un solo pronóstico por símbolo
    assert [s for s, _ in kronos.forecasts] == ["BTCUSDT", "ETHUSDT"]
    k = by[("kronos", "BTCUSDT")]
    assert k.state is StrategyState.SHADOW and k.is_shadow
    assert k.hard_stop is True
    assert k.sim is not None and k.sim.source == "combined"


def test_veto_is_attached_and_failures_are_isolated(frames, gate):
    store = InMemoryVetoStore()
    store.put(
        Veto(
            scope="BTC",
            impact=Impact.HIGH,
            reason="hackeo de un exchange",
            article_id="a1",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    registry = registry_with(trend=StrategyState.ACTIVE, broken=StrategyState.PROBATION)

    def factory(record: StrategyRecord) -> BaseAgent:
        if record.name == "broken":
            raise RuntimeError("sin código")
        return default_agent_factory(record)

    layer = SharedSignalLayer(
        registry,
        FrameBarsProvider(frames),
        MemorySink(),
        gate=gate,
        news=StoreVetoProvider(store),
        agent_factory=factory,
    )
    events = layer.on_bar_close(START + timedelta(days=300))
    vetoes = {e.symbol: e.veto for e in events}
    assert vetoes["BTCUSDT"].reason == "hackeo de un exchange"
    assert vetoes["ETHUSDT"] is None
    assert any("broken" in err for err in layer.errors)
    assert Timeframe.D1 in layer.timeframes()


def test_veto_provider_errors_fail_open(frames, gate):
    class Boom:
        def active_veto(self, symbol):
            raise ConnectionError("redis caído")

    layer = SharedSignalLayer(
        registry_with(trend=StrategyState.ACTIVE),
        FrameBarsProvider(frames),
        MemorySink(),
        gate=gate,
        news=Boom(),
    )
    assert all(e.veto is None for e in layer.on_bar_close(START + timedelta(days=300)))


def test_gate_errors_and_missing_bars(gate):
    short = {("BTCUSDT", Timeframe.D1): daily(100 * 1.01 ** np.arange(30))}
    layer = SharedSignalLayer(
        registry_with(trend=StrategyState.ACTIVE),
        FrameBarsProvider(short),
        MemorySink(),
        gate=gate,
        policies={"trend": SleevePolicy(gate_horizon=500)},
    )
    events = layer.on_bar_close(START + timedelta(days=30))
    assert [e.symbol for e in events] == ["BTCUSDT"]  # ETH no tiene velas
    ev = events[0]
    if ev.signal.direction is Direction.LONG:
        assert ev.sim is None and ev.sim_error


def test_run_offline_is_reproducible_and_applies_commands(frames, gate, make_bot):
    def run():
        registry = registry_with(trend=StrategyState.ACTIVE)
        layer = SharedSignalLayer(registry, FrameBarsProvider(frames), MemorySink(), gate=gate)
        low, _, _ = make_bot("low", risk_level=1)
        high, _, _ = make_bot("high", risk_level=5)
        kill_at = START + timedelta(days=320, hours=1)
        return run_offline(
            layer,
            [low, high],
            START + timedelta(days=299),
            START + timedelta(days=330),
            commands=[(kill_at, BotCommand(CommandType.KILL, "high", "fin"))],
        )

    a, b = run(), run()
    dump = lambda r: [d.model_dump(mode="json") for d in r.decisions]  # noqa: E731
    assert dump(a) == dump(b)
    assert len(a.events) == 31 * 2
    buys = {d.bot_id: d.decision.quantity for d in a.decisions if d.decision.side is OrderSide.BUY}
    assert buys["high"] > buys["low"]
    kills = [d for d in a.decisions if d.bot_id == "high" and d.decision.side is OrderSide.SELL]
    assert kills and all(d.target_quantity == 0 for d in kills)


def test_bar_close_helpers():
    assert bar_closed(datetime(2024, 1, 2, tzinfo=UTC), Timeframe.D1)
    assert not bar_closed(datetime(2024, 1, 2, 4, tzinfo=UTC), Timeframe.D1)
    assert bar_closed(datetime(2024, 1, 2, 4), Timeframe.H4)  # sin zona = UTC
    instants = bar_close_instants(
        datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC), [Timeframe.H4]
    )
    assert len(instants) == 6 and instants[-1] == datetime(2024, 1, 2, tzinfo=UTC)


def test_frame_provider_has_no_look_ahead(frames):
    provider = FrameBarsProvider(frames)
    bars = provider.bars("BTCUSDT", Timeframe.D1, START + timedelta(days=10, hours=3))
    assert bars.index[-1] == pd.Timestamp(START + timedelta(days=9))
    assert provider.bars("XRPUSDT", Timeframe.D1, START).empty


def test_marketdata_provider_drops_the_open_bar(frames):
    calls = []

    class FakeService:
        def get_bars(self, symbol, interval, start, end):
            calls.append((symbol, interval))
            return frames[(symbol, Timeframe.D1)]

    provider = MarketDataBarsProvider(FakeService())
    bars = provider.bars("BTCUSDT", Timeframe.D1, START + timedelta(days=5, hours=1))
    assert bars.index[-1] == pd.Timestamp(START + timedelta(days=4))
    assert calls == [("BTCUSDT", "1d")]


def test_store_veto_provider_prefers_global():
    store = InMemoryVetoStore()
    exp = datetime.now(UTC) + timedelta(hours=1)
    store.put(Veto(scope="*", impact=Impact.HIGH, reason="global", article_id="g", expires_at=exp))
    assert StoreVetoProvider(store).active_veto("ETHUSDT").reason == "global"
    assert StoreVetoProvider(InMemoryVetoStore()).active_veto("ETHUSDT") is None


def test_policies_and_levels():
    assert policy_for("kronos").signal_levels
    assert policy_for("nueva") == SleevePolicy()
    custom = SleevePolicy(gate_horizon=3)
    assert policy_for("trend", {"trend": custom}) is custom
    sig = Signal(
        symbol="BTCUSDT",
        direction=Direction.LONG,
        confidence=1,
        horizon=Horizon.SHORT,
        timeframe=Timeframe.H4,
        source="kronos",
        metadata={"stop_loss": "x", "take_profit": 1},
    )
    sl, tp, hard = policy_for("kronos").levels(sig, Decimal("100"))
    assert (sl, tp, hard) == (Decimal("5.00"), Decimal("800.00"), False)  # niveles inválidos
    stop, take = sell_levels(Decimal("100"))
    assert take < Decimal("100") < stop


def test_default_factory_uses_params_and_rejects_unknown():
    record = StrategyRecord("trend", params={"enabled": True, "lookbacks": [20, 50]})
    agent = default_agent_factory(record)
    assert agent.timeframe is Timeframe.D1
    with pytest.raises(KeyError):
        default_agent_factory(StrategyRecord("no_existe"))
