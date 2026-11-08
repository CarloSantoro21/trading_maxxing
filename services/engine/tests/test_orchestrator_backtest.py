"""El orquestador (#19) y el motor juntos en backtest: mismas piezas que en vivo, sin Redis.

La capa compartida de `tm_agents` evalúa el TrendAgent real sobre velas diarias, dos bots con
distinto nivel de riesgo dimensionan las mismas señales y sus decisiones se ejecutan en el
BacktestEngine con el venue simulado de Binance Spot (comisión de 0.1 %).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
from tm_agents.orchestrator.bot import BotConfig, BotProfile, BotRunner
from tm_agents.orchestrator.runtime import run_offline
from tm_agents.orchestrator.shared import FrameBarsProvider, SharedSignalLayer
from tm_agents.orchestrator.streams import MemorySink
from tm_agents.registry import InMemoryBackend, StrategyRegistry
from tm_agents.simulation_gate import GateConfig, SimulationGate
from tm_engine.backtest import run_backtest
from tm_engine.decisions import ScheduledDecisionSource
from tm_shared.contracts import OrderSide, Timeframe

START = datetime(2023, 1, 1, tzinfo=UTC)
REPORT = {
    "aggregate": {"volatility": 0.38, "sharpe": 0.98},
    "montecarlo": {"max_drawdown": {"p95": 0.615}},
}


def _daily() -> pd.DataFrame:
    t = np.arange(240)
    closes = 100 * np.exp(np.cumsum(0.004 + 0.025 * np.sin(t / 6)))
    opens = np.r_[closes[0], closes[:-1]]
    idx = pd.date_range(START, periods=len(t), freq="D", tz="UTC", name="open_time")
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


def _orchestrate(frame: pd.DataFrame):
    registry = StrategyRegistry(InMemoryBackend())
    registry.register("trend", {"lookbacks": [5, 10, 20, 40]})
    registry.backend.update("trend", {"state": "active", "lab_report": REPORT})
    layer = SharedSignalLayer(
        registry,
        FrameBarsProvider({("BTCUSDT", Timeframe.D1): frame}),
        MemorySink(),
        symbols=["BTCUSDT"],
        gate=SimulationGate(GateConfig(n_paths=200, fee_rate=0.001)),
    )
    runners = [
        BotRunner(
            BotConfig(
                bot_id,
                BotProfile(
                    risk_level=level,
                    capital=Decimal("10000"),
                    max_drawdown=0.5,
                    instruments=("BTCUSDT",),
                ),
            ),
            MemorySink(),
        )
        for bot_id, level in (("low", 1), ("high", 5))
    ]
    result = run_offline(layer, runners, START + timedelta(days=60), START + timedelta(days=239))
    return result, runners


def test_orchestrator_decisions_run_in_the_backtest_engine():
    frame = _daily()
    offline, runners = _orchestrate(frame)
    decisions = offline.decisions
    assert {d.bot_id for d in decisions} == {"low", "high"}
    partial_sells = [
        d for d in decisions if d.decision.side is OrderSide.SELL and d.target_quantity > 0
    ]
    assert partial_sells, "el Trend debe producir ventas parciales"

    result = run_backtest(
        {"BTCUSDT": frame}, ScheduledDecisionSource(decisions), timeframe=Timeframe.D1
    )
    assert result.rejections == []
    assert {"entry", "reduce", "exit"} <= {f.role for f in result.fills}
    # el libro de cada bot en el motor coincide con su libro virtual
    for runner in runners:
        held = {sym: p.quantity for _s, sym, p in runner.book.held()}
        engine = result.sleeves[f"{runner.bot_id}/trend"]["positions"]
        assert {sym: Decimal(p["quantity"]) for sym, p in engine.items()} == held
    low = result.sleeves["low/trend"]["positions"]["BTCUSDT"]["quantity"]
    high = result.sleeves["high/trend"]["positions"]["BTCUSDT"]["quantity"]
    assert Decimal(high) > 4 * Decimal(low)


def test_orchestrated_backtest_is_reproducible():
    frame = _daily()
    a, _ = _orchestrate(frame)
    b, _ = _orchestrate(frame)
    assert [d.model_dump(mode="json") for d in a.decisions] == [
        d.model_dump(mode="json") for d in b.decisions
    ]
