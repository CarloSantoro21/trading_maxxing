"""Endurecimiento del orquestador (#89), de punta a punta con el motor de backtest.

1. Una estrategia `active` con posición abierta: el LifecycleMonitor la retira, el BotRunner
   emite la venta de cierre, el `TradingSleeveGuard` la deja pasar (es un cierre) y el motor la
   ejecuta. La posición del motor y el libro virtual del bot quedan en 0, y una entrada
   posterior de la estrategia retirada se sigue bloqueando.
2. Reinicio del worker: el motor escribe sus fills en `trades` (PostgREST simulado en memoria)
   y, al arrancar de nuevo, el supervisor reconstruye el libro de cada bot desde ahí y el motor
   restaura su libro por bot y sleeve.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from tm_agents.lifecycle import LifecycleMonitor, LiveStats
from tm_agents.montecarlo.report import DistributionSummary
from tm_agents.orchestrator.bot import BotConfig, BotProfile, BotRunner
from tm_agents.orchestrator.shared import FrameBarsProvider, SharedSignalLayer
from tm_agents.orchestrator.state import PostgRESTTradeStateRepository
from tm_agents.orchestrator.streams import MemorySink
from tm_agents.orchestrator.supervisor import BotSupervisor, InMemoryBotRepository
from tm_agents.reference import TradingSleeveGuard
from tm_agents.registry import InMemoryBackend, StrategyRegistry
from tm_agents.simulation_gate import GateConfig, SimulationGate
from tm_agents.strategies.trend import TrendAgent
from tm_agents.strategy_stats import StrategyState as S
from tm_engine.backtest import build_engine, run_backtest
from tm_engine.decisions import ScheduledDecisionSource
from tm_engine.instruments import bar_type
from tm_engine.recorder import SupabaseTradeRecorder
from tm_engine.service import TradingEngineService
from tm_engine.strategy import AgenticStrategy, AgenticStrategyConfig
from tm_shared.contracts import DecisionEnvelope, OrderSide, Timeframe

START = datetime(2023, 1, 1, tzinfo=UTC)
HUMAN = "11111111-1111-1111-1111-111111111111"
REPORT = {
    "aggregate": {"volatility": 0.38, "sharpe": 0.98},
    "montecarlo": {"max_drawdown": {"p95": 0.615, "p99": 0.7}},
}
LOOKBACKS = [5, 10, 20, 40]


def _daily(days: int = 240) -> pd.DataFrame:
    t = np.arange(days)
    closes = 100 * np.exp(np.cumsum(0.004 + 0.025 * np.sin(t / 6)))
    opens = np.r_[closes[0], closes[:-1]]
    idx = pd.date_range(START, periods=days, freq="D", tz="UTC", name="open_time")
    return pd.DataFrame(
        {
            "open": opens,
            "high": np.maximum(opens, closes) * 1.01,
            "low": np.minimum(opens, closes) * 0.99,
            "close": closes,
            "volume": 1.0,
        },
        index=idx,
    )


def _registry() -> StrategyRegistry:
    """`trend` en active y una copia en shadow (sigue publicando eventos tras el retiro)."""
    registry = StrategyRegistry(InMemoryBackend())
    for name, final in (("trend", S.ACTIVE), ("trend_shadow", S.SHADOW)):
        registry.register(name, {"lookbacks": LOOKBACKS})
        registry.backend.update(name, {"lab_report": REPORT})
        for to in (S.LAB, S.SHADOW, S.ACTIVE)[: (S.LAB, S.SHADOW, S.ACTIVE).index(final) + 1]:
            registry.transition(name, to, HUMAN if to is S.ACTIVE else "system", "prueba")
    return registry


def _layer(registry: StrategyRegistry, frame: pd.DataFrame) -> SharedSignalLayer:
    return SharedSignalLayer(
        registry,
        FrameBarsProvider({("BTCUSDT", Timeframe.D1): frame}),
        MemorySink(),
        symbols=["BTCUSDT"],
        gate=SimulationGate(GateConfig(n_paths=200, fee_rate=0.001)),
        agents={"trend_shadow": TrendAgent(lookbacks=LOOKBACKS)},
    )


def _config(bot_id: str = "bot-a", level: int = 5) -> BotConfig:
    profile = BotProfile(
        risk_level=level, capital=Decimal("10000"), max_drawdown=0.9, instruments=("BTCUSDT",)
    )
    return BotConfig(bot_id, profile)


@dataclass(frozen=True)
class _Band:
    """Distribución del drawdown del MC (lo que lee el LifecycleMonitor)."""

    max_drawdown: DistributionSummary


BAND = _Band(DistributionSummary(0.3, 0.1, 0.0, 0.9, 0.1, 0.3, 0.615, 0.7))


class RetireWhenHolding:
    """Hook: la primera vez que el bot tiene `trend`, el monitor la degrada hasta `retired`."""

    def __init__(self, registry: StrategyRegistry, runner: BotRunner, after: datetime) -> None:
        self.registry, self.runner, self.after = registry, runner, after
        self.monitor = LifecycleMonitor()
        self.retired_at: datetime | None = None
        self.held_before: Decimal = Decimal("0")

    def __call__(self, now: datetime, _orchestration) -> None:
        held = self.runner.book.position("trend", "BTCUSDT").quantity
        if self.retired_at is not None or now < self.after or held <= 0:
            return
        record = self.registry.get("trend")
        # Drawdown en vivo por encima del p99: active -> probation -> retired.
        stats = LiveStats(1.0, 2.0, (), record.state_changed_at or now, now, 0.8)
        while transition := self.monitor.check(self.registry.get("trend").state, stats, BAND):
            self.registry.transition("trend", transition.to, transition.actor, transition.reason)
        assert self.registry.get("trend").state is S.RETIRED
        self.retired_at, self.held_before = now, held


def test_retired_strategy_position_is_closed_by_the_engine(live_orchestration):
    frame = _daily()
    registry = _registry()
    runner = BotRunner(_config(), MemorySink(), shadow_sink=MemorySink())
    retire = RetireWhenHolding(registry, runner, START + timedelta(days=120))
    orchestration = live_orchestration(
        _layer(registry, frame), [runner], start=START + timedelta(days=60), hooks=[retire]
    )
    guard = TradingSleeveGuard(registry, ttl=0)  # lee el estado en cada decisión
    result = run_backtest(
        {"BTCUSDT": frame}, orchestration, timeframe=Timeframe.D1, sleeve_guard=guard
    )

    assert retire.retired_at is not None and retire.held_before > 0
    closes = [
        d
        for d in orchestration.decisions
        if d.sleeve == "trend" and d.decision.decided_at == retire.retired_at
    ]
    assert len(closes) == 1
    (close,) = closes
    assert close.decision.side is OrderSide.SELL and close.target_quantity == 0
    assert close.decision.quantity == retire.held_before
    # el motor la ejecutó: fill de salida por toda la posición, sin rechazos
    assert result.rejections == []
    exits = [f for f in result.fills if f.role == "exit" and f.ts >= retire.retired_at]
    assert {(f.bot_id, f.sleeve, f.side) for f in exits} == {("bot-a", "trend", OrderSide.SELL)}
    assert sum(f.quantity for f in exits) == retire.held_before  # puede llenarse en partes
    assert exits[-1].position_after == 0
    assert all(f.ts < retire.retired_at for f in result.fills if f not in exits)
    # posición del motor y libro virtual en 0; ninguna orden de trend después del retiro
    assert result.sleeves["bot-a/trend"]["positions"] == {}
    assert runner.book.position("trend", "BTCUSDT").quantity == 0
    assert not [d for d in orchestration.decisions if d.decision.decided_at > retire.retired_at]


def test_retired_sleeve_entries_are_still_blocked(make_frame, envelope):
    registry = _registry()
    registry.transition("trend", S.RETIRED, "system", "prueba")
    entry = envelope(sleeve="trend", minute=1)
    result = run_backtest(
        {"BTCUSDT": make_frame([100, 101, 102, 103])},
        ScheduledDecisionSource([entry]),
        sleeve_guard=TradingSleeveGuard(registry),
    )
    assert result.orders == [] and result.fills == []
    assert len(result.rejections) == 1 and "retired" in result.rejections[0]


def test_close_passes_when_the_registry_fails(make_frame, envelope):
    """Falla cerrado para las entradas, pero el cierre llega al venue igual."""

    class Broken(StrategyRegistry):
        def find(self, name):  # type: ignore[override]
            from tm_agents.registry import RegistryError

            raise RegistryError("supabase caído")

    calls: list[str] = []
    healthy = TradingSleeveGuard(_registry())

    def flaky(sleeve: str) -> bool:
        # Abre con el registro sano; desde el minuto 2 el registro falla.
        calls.append(sleeve)
        guard = healthy if len(calls) == 1 else TradingSleeveGuard(Broken())
        return guard(sleeve)

    decisions = [
        envelope(sleeve="trend", minute=1),
        envelope(sleeve="trend", minute=2),  # entrada: el registro falla y se bloquea
        envelope(OrderSide.SELL, sleeve="trend", minute=3),  # cierre: pasa igual
    ]
    result = run_backtest(
        {"BTCUSDT": make_frame([100, 101, 102, 103, 104, 105])},
        ScheduledDecisionSource(decisions),
        sleeve_guard=flaky,
    )
    assert [f.role for f in result.fills] == ["entry", "exit"]
    assert len(result.rejections) == 1 and result.rejections[0].startswith("sin órdenes")
    assert result.sleeves["trend"]["positions"] == {}
    assert calls == ["trend", "trend"]  # el cierre ni siquiera consulta al guardia


# --- reinicio --------------------------------------------------------------------------


def _first_run(frame, trades_table, live_orchestration, days: int):
    """Corre el motor y el orquestador `days` días con el recorder escribiendo en `trades`."""
    registry = _registry()
    configs = [_config("bot-a", 5), _config("bot-b", 2)]
    runners = [BotRunner(c, MemorySink(), shadow_sink=MemorySink()) for c in configs]
    orchestration = live_orchestration(
        _layer(registry, frame), runners, start=START + timedelta(days=60)
    )
    recorder = SupabaseTradeRecorder(url="http://db", service_key="k", client=trades_table.client())
    engine = build_engine({"BTCUSDT": frame.iloc[:days]}, timeframe=Timeframe.D1)
    strategy = AgenticStrategy(
        AgenticStrategyConfig(bar_types=(str(bar_type("BTCUSDT", Timeframe.D1)),)),
        source=orchestration,
        recorder=recorder,
    )
    engine.add_strategy(strategy)
    try:
        engine.run()
        report = strategy.sleeve_report()
    finally:
        engine.dispose()
    return configs, runners, report, orchestration


def test_worker_restart_keeps_each_bot_positions(trades_table, live_orchestration):
    frame = _daily()
    configs, before, engine_report, orchestration = _first_run(
        frame, trades_table, live_orchestration, days=200
    )
    sides = {(d.decision.side, bool(d.target_quantity)) for d in orchestration.decisions}
    assert (OrderSide.SELL, True) in sides, "debe haber ventas parciales antes del reinicio"
    held = {r.bot_id: {(s, sym): p for s, sym, p in r.book.held()} for r in before}
    assert all(held.values()), "los dos bots deben terminar con posición abierta"
    open_rows = [r for r in trades_table.rows if r["closed_at"] is None]
    assert {r["bot_id"] for r in open_rows} == {"bot-a", "bot-b"}
    assert any(r["closed_at"] for r in trades_table.rows), "también hay operaciones cerradas"

    # Reinicio: un supervisor nuevo, con los mismos bots y la tabla trades como fuente.
    repo = PostgRESTTradeStateRepository("http://db", "k", client=trades_table.client(), page=2)
    supervisor = BotSupervisor(
        InMemoryBotRepository(configs), MemorySink(), shadow_sink=MemorySink(), trades=repo
    )
    supervisor.sync()
    assert set(supervisor.runners) == {"bot-a", "bot-b"}
    for old in before:
        new = supervisor.runners[old.bot_id]
        restored = {(s, sym): p for s, sym, p in new.book.held()}
        engine = engine_report[f"{old.bot_id}/trend"]
        # las posiciones son las del motor (fills reales) y coinciden con el libro de antes
        assert restored.keys() == held[old.bot_id].keys() == {("trend", "BTCUSDT")}
        pos = restored[("trend", "BTCUSDT")]
        real = engine["positions"]["BTCUSDT"]
        assert (
            pos.quantity
            == Decimal(real["quantity"])
            == held[old.bot_id][("trend", "BTCUSDT")].quantity
        )
        assert pos.avg_price == pytest.approx(Decimal(real["avg_price"]))
        assert pos.fraction == pytest.approx(
            held[old.bot_id][("trend", "BTCUSDT")].fraction, abs=1e-3
        )
        assert pos.stop_loss is None  # trend: el bracket es de catástrofe
        # efectivo = capital + P&L realizado del motor (con comisiones) - costo de lo abierto
        expected_cash = (
            Decimal("10000") + Decimal(engine["realized_pnl"]) - pos.quantity * pos.avg_price
        )
        assert new.book.cash == pytest.approx(expected_cash, abs=Decimal("1e-6"))

    # Las señales siguientes se dimensionan sobre lo restaurado: la posición objetivo de la
    # primera decisión de cada bot parte de la cantidad que ya tenía (no vuelve a comprar todo).
    layer = _layer(_registry(), frame)
    first: dict[str, DecisionEnvelope] = {}
    for day in range(201, 240):
        for event in layer.on_bar_close(START + timedelta(days=day)):
            for out in supervisor.on_event(event):
                if out.sleeve == "trend" and not out.shadow:
                    first.setdefault(out.bot_id, out)
    assert first, "debe haber decisiones después del reinicio"
    for bot_id, decision in first.items():
        had = held[bot_id][("trend", "BTCUSDT")].quantity
        sign = 1 if decision.decision.side is OrderSide.BUY else -1
        assert decision.target_quantity == had + sign * decision.decision.quantity


def test_engine_restores_its_book_from_trades(trades_table, live_orchestration):
    _configs, _runners, report, _o = _first_run(
        _daily(), trades_table, live_orchestration, days=200
    )
    recorder = SupabaseTradeRecorder(url="http://db", service_key="k", client=trades_table.client())
    service = TradingEngineService(credentials=None, recorder=recorder)
    service.start()
    try:
        assert service.status()["restored_positions"] == 2
        assert service.status()["restore_error"] is None
        restored = service.strategy.sleeve_report()
        for book in ("bot-a/trend", "bot-b/trend"):
            assert restored[book]["positions"] == {
                sym: {"quantity": p["quantity"], "avg_price": p["avg_price"]}
                for sym, p in report[book]["positions"].items()
            }
    finally:
        service.stop()
    # El recorder retoma la fila abierta: el cierre después del reinicio cierra esa fila.
    key = ("bot-a", "trend", "BTCUSDT")
    assert key in recorder._open


def test_restored_position_can_be_closed_after_engine_restart(make_frame, envelope):
    from tm_engine.recorder import RestoredPosition

    engine = build_engine({"BTCUSDT": make_frame([100, 101, 102, 103])}, starting_usdt=100_000)
    sell = envelope(OrderSide.SELL, minute=1).model_copy(
        update={"bot_id": "bot-a", "target_quantity": Decimal("0")}
    )
    strategy = AgenticStrategy(
        AgenticStrategyConfig(bar_types=(str(bar_type("BTCUSDT", Timeframe.M1)),)),
        source=ScheduledDecisionSource([sell]),
    )
    restored = [RestoredPosition("bot-a", "trend", "BTCUSDT", Decimal("1"), Decimal("100"))]
    assert strategy.restore_positions(restored) == 1
    assert strategy.restore_positions([RestoredPosition(None, "x", "BTCUSDT", 0, 1)]) == 0
    engine.add_strategy(strategy)
    try:
        engine.run()
        # La decisión de cierre encuentra su posición (antes del #89 se rechazaba).
        assert [r.reason for r in strategy.rejections] == []
        assert strategy.accepted == [sell]
    finally:
        engine.dispose()


def test_restore_failure_is_reported_and_engine_still_starts():
    class Failing:
        def on_fill(self, record) -> None: ...

        def open_positions(self):
            raise RuntimeError("sin red")

    service = TradingEngineService(credentials=None, recorder=Failing())
    service.start()
    try:
        assert "sin red" in service.status()["restore_error"]
        assert service.status()["restored_positions"] == 0
    finally:
        service.stop()
