"""Fixtures comunes de tm_agents.

Ninguna prueba ve Redis, Supabase ni llaves del entorno. Los constructores de eventos y bots
del orquestador (#19) se exponen como fixtures (`make_event`, `make_bot`, `make_snapshot`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from tm_agents.orchestrator.bot import BotConfig, BotProfile, BotRunner
from tm_agents.orchestrator.streams import MemorySink
from tm_agents.signals import SignalEvent, StrategySnapshot, VetoInfo, event_id
from tm_agents.strategy_stats import StrategyState
from tm_shared.contracts import Direction, Horizon, Signal, SimResult, Timeframe

T0 = datetime(2024, 3, 1, tzinfo=UTC)
SIM_OK = SimResult(
    p_take_profit=0.3, p_stop_loss=0.1, p_open=0.6, expected_value=0.4, n_paths=100, source="x"
)


def snapshot(
    name: str = "trend",
    state: StrategyState = StrategyState.ACTIVE,
    *,
    p95: float | None = 0.615,
    volatility: float = 0.38,
) -> StrategySnapshot:
    return StrategySnapshot(
        name=name, state=state, volatility=volatility, oos_sharpe=0.98, max_drawdown_p95=p95
    )


def event(
    confidence: float = 1.0,
    *,
    strategy: str = "trend",
    state: StrategyState = StrategyState.ACTIVE,
    price: str = "50000",
    day: int = 0,
    symbol: str = "BTCUSDT",
    veto: bool = False,
    sim: SimResult | None = SIM_OK,
    catalog: tuple[StrategySnapshot, ...] | None = None,
    hard_stop: bool = False,
    stop_loss: str | None = None,
    take_profit: str | None = None,
) -> SignalEvent:
    when = T0 + timedelta(days=day)
    direction = Direction.LONG if confidence > 0 else Direction.FLAT
    p = Decimal(price)
    signal = Signal(
        symbol=symbol,
        direction=direction,
        confidence=confidence,
        horizon=Horizon.MEDIUM,
        timeframe=Timeframe.D1,
        source=strategy,
        generated_at=when,
    )
    return SignalEvent(
        id=event_id(strategy, symbol, when),
        strategy=strategy,
        state=state,
        symbol=symbol,
        bar_close=when,
        price=p,
        signal=signal,
        veto=VetoInfo(scope="BTC", impact="high", reason="hackeo", expires_at=when + timedelta(1))
        if veto
        else None,
        sim=sim if direction is Direction.LONG else None,
        stop_loss=Decimal(stop_loss) if stop_loss else (p * Decimal("0.05")).quantize(Decimal(1)),
        take_profit=Decimal(take_profit) if take_profit else p * 8,
        hard_stop=hard_stop,
        catalog=catalog if catalog is not None else (snapshot(strategy, state),),
    )


def bot(
    bot_id: str = "bot-a",
    *,
    risk_level: int = 3,
    capital: str = "10000",
    max_drawdown: float = 0.5,
    strategy_config: dict | None = None,
) -> tuple[BotRunner, MemorySink, MemorySink]:
    sink, shadow = MemorySink(), MemorySink()
    config = BotConfig(
        bot_id=bot_id,
        profile=BotProfile(
            risk_level=risk_level, capital=Decimal(capital), max_drawdown=max_drawdown
        ),
        strategy_config=strategy_config or {},
    )
    return BotRunner(config, sink, shadow_sink=shadow), sink, shadow


@pytest.fixture
def make_event():
    return event


@pytest.fixture
def make_bot():
    return bot


@pytest.fixture
def make_snapshot():
    return snapshot


@pytest.fixture(autouse=True)
def _no_external_services(monkeypatch):
    for var in (
        "REDIS_URL",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "GROQ_API_KEY",
        "CRYPTOPANIC_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
