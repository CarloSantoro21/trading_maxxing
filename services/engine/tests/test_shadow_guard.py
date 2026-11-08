"""Shadow nunca envía órdenes (#57) y el ledger de referencia coincide con el laboratorio.

- ``AgenticStrategy`` con ``TradingSleeveGuard``: las decisiones de un sleeve en shadow se
  rechazan antes de crear cualquier orden; las de uno activo se ejecutan.
- ``ReferenceLedger`` y ``simulate_sleeve`` del laboratorio dan el mismo equity y los mismos
  trades con las mismas señales (sin el redondeo de cantidades de Binance).
"""

from __future__ import annotations

from datetime import UTC, timedelta

import numpy as np
import pandas as pd
import pytest
from tm_agents.reference import ReferenceLedger, SignalEnvelope, TradingSleeveGuard
from tm_agents.registry import StrategyRegistry
from tm_agents.strategy_stats import StrategyState as S
from tm_engine.backtest import run_backtest
from tm_engine.decisions import ScheduledDecisionSource
from tm_engine.lab.simulator import simulate_sleeve
from tm_engine.service import EngineProfile, TradingEngineService
from tm_shared.contracts import Direction, Horizon, OrderSide, Signal, Timeframe

HUMAN = "11111111-1111-1111-1111-111111111111"
UP = [100, 100, 101, 102, 104, 106, 108, 111, 112]


def registry_with(**states: S) -> StrategyRegistry:
    registry = StrategyRegistry()
    path = [S.LAB, S.SHADOW, S.ACTIVE]
    for name, state in states.items():
        registry.register(name)
        for to in path[: path.index(state) + 1]:
            registry.transition(name, to, HUMAN if to is S.ACTIVE else "system", "prueba")
    return registry


def test_shadow_sleeve_never_reaches_the_venue(make_frame, envelope):
    guard = TradingSleeveGuard(registry_with(kronos=S.SHADOW, trend=S.ACTIVE))
    decisions = [
        envelope(sleeve="kronos", minute=1),
        envelope(sleeve="trend", minute=1),
        envelope(OrderSide.SELL, sleeve="kronos", minute=3),
    ]
    result = run_backtest(
        {"BTCUSDT": make_frame(UP)}, ScheduledDecisionSource(decisions), sleeve_guard=guard
    )
    assert not any("sleeve:kronos" in o.tags for o in result.orders)
    assert all(f.sleeve == "trend" for f in result.fills) and result.fills
    shadow = [r for r in result.rejections if "kronos" in r]
    assert len(shadow) == 2
    assert all(r.startswith("sin órdenes reales") and "shadow" in r for r in shadow)
    assert "kronos" not in result.sleeves


def test_unknown_sleeve_is_blocked_and_plain_callables_work(make_frame, envelope):
    result = run_backtest(
        {"BTCUSDT": make_frame(UP)},
        ScheduledDecisionSource([envelope(sleeve="trend")]),
        sleeve_guard=lambda sleeve: False,
    )
    assert result.orders == [] and result.fills == []
    assert result.rejections == ["sin órdenes reales: el sleeve no opera capital"]


def test_engine_service_passes_guard_to_strategy():
    guard = TradingSleeveGuard(StrategyRegistry())
    svc = TradingEngineService(credentials=None, sleeve_guard=guard)
    svc.start(EngineProfile())
    assert svc.strategy is not None and svc.strategy._sleeve_guard is guard
    svc.stop()


def _signal(ts: pd.Timestamp, long: bool, close: float) -> SignalEnvelope:
    sig = Signal(
        symbol="BTCUSDT",
        direction=Direction.LONG if long else Direction.FLAT,
        confidence=1.0 if long else 0.0,
        horizon=Horizon.SHORT,
        timeframe=Timeframe.D1,
        source="kronos",
        generated_at=ts.to_pydatetime(),
    )
    return SignalEnvelope(signal=sig, close=close)


def test_reference_ledger_matches_lab_simulator(daily_bars):
    bars = daily_bars("2025-01-01", "2025-03-01", lambda t: 100 + 10 * np.sin(t / 5) + t * 0.2)
    period = pd.Timedelta(days=1)
    target = pd.Series((np.arange(len(bars)) // 7) % 2, index=bars.index, dtype=float)
    lab = simulate_sleeve(
        bars, target, capital=100_000, fee=0.001, period=period, sleeve="kronos",
        symbol="BTCUSDT", qty_decimals=12, cash_buffer=0.0,
    )  # fmt: skip
    start = (bars.index[0]).to_pydatetime().astimezone(UTC) - timedelta(seconds=1)
    ledger = ReferenceLedger("kronos", start, symbols=("BTCUSDT",), sizing="full")
    closed = []
    equity = []
    for (open_ts, row), tgt in zip(bars.iterrows(), target, strict=True):
        res = ledger.on_signal(_signal(open_ts + period, tgt > 0.5, float(row["close"])))
        closed.extend(res.closed)
        equity.append(ledger.equity)
    np.testing.assert_allclose(equity, lab.equity.to_numpy(), rtol=1e-9)
    lab_closed = [t for t in lab.trades if not t.is_open]
    assert len(closed) == len(lab_closed) > 2
    for mine, theirs in zip(closed, lab_closed, strict=True):
        assert mine.ret == pytest.approx(theirs.ret, rel=1e-9)
        assert mine.pnl == pytest.approx(theirs.pnl, rel=1e-9)
