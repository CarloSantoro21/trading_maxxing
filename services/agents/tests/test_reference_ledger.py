"""Ledger de referencia (#57): fills al cierre, comisión, trades y estado serializable."""

from __future__ import annotations

import itertools
import json
import math
from datetime import UTC, datetime, timedelta

import pytest
from tm_agents.reference.ledger import (
    ReferenceLedger,
    daily_sharpe,
    max_drawdown,
    target_exposure,
)
from tm_agents.reference.signals import SignalEnvelope
from tm_shared.contracts import Direction, Horizon, OrderSide, Signal, Timeframe

T0 = datetime(2026, 11, 1, tzinfo=UTC)
FEE = 0.001


def env(
    strategy: str = "kronos",
    direction: Direction = Direction.LONG,
    close: float | None = 100.0,
    *,
    hours: float = 0,
    symbol: str = "BTCUSDT",
    confidence: float = 1.0,
    timeframe: Timeframe = Timeframe.H4,
) -> SignalEnvelope:
    ts = T0 + timedelta(hours=hours)
    sig = Signal(
        symbol=symbol,
        direction=direction,
        confidence=confidence if direction is not Direction.FLAT else 0.0,
        horizon=Horizon.SHORT,
        timeframe=timeframe,
        source=strategy,
        generated_at=ts,
    )
    return SignalEnvelope(signal=sig, close=close)


def test_full_sizing_round_trip_matches_lab_formula():
    led = ReferenceLedger("kronos", T0, capital=100_000, symbols=("BTCUSDT",))
    assert led.sizing == "full"
    buy = led.on_signal(env(close=100.0))
    (fill,) = buy.fills
    assert fill.side is OrderSide.BUY
    # Compra con todo el efectivo, comisión incluida
    assert fill.quantity == pytest.approx(100_000 / (100 * (1 + FEE)))
    assert fill.commission == pytest.approx(fill.quantity * 100 * FEE)
    assert led.cash == pytest.approx(0.0, abs=1e-6)
    sell = led.on_signal(env(direction=Direction.FLAT, close=110.0, hours=4))
    (trade,) = sell.closed
    q = fill.quantity
    cost = q * 100 * (1 + FEE)
    exit_value = q * 110 * (1 - FEE)
    assert trade.pnl == pytest.approx(exit_value - cost)
    assert trade.ret == pytest.approx((exit_value - cost) / cost)
    assert trade.fees == pytest.approx(q * 100 * FEE + q * 110 * FEE)
    assert trade.entry_price == pytest.approx(100.0)
    assert trade.exit_price == pytest.approx(110.0)
    assert trade.closed_at == T0 + timedelta(hours=4)
    assert led.equity == pytest.approx(exit_value)
    assert led.n_trades == 1 and led.n_wins == 1
    assert led.commissions == pytest.approx(trade.fees)


def test_same_target_does_not_trade_and_price_only_marks():
    led = ReferenceLedger("kronos", T0, symbols=("BTCUSDT",))
    led.on_signal(env(close=100.0))
    again = led.on_signal(env(close=120.0, hours=4))
    assert again.fills == [] and again.closed == []
    assert led.peak_equity == pytest.approx(led.equity)
    down = led.on_signal(env(close=90.0, hours=8))
    assert down.fills == []
    assert led.drawdown == pytest.approx(1 - 90 / 120, rel=1e-6)


def test_confidence_sizing_partial_fills_one_trade():
    led = ReferenceLedger("trend", T0, capital=50_000, symbols=("BTCUSDT",))
    assert led.sizing == "confidence"
    r1 = led.on_signal(env("trend", confidence=0.25, close=100.0, timeframe=Timeframe.D1))
    assert r1.fills[0].quantity * 100 == pytest.approx(0.25 * 50_000, rel=1e-3)
    r2 = led.on_signal(env("trend", confidence=0.5, close=100.0, hours=24))
    assert r2.fills[0].side is OrderSide.BUY
    exposure = led.metrics()["exposure"]["BTCUSDT"]
    assert exposure == pytest.approx(0.5, rel=1e-3)
    r3 = led.on_signal(env("trend", confidence=0.25, close=100.0, hours=48))
    assert r3.fills[0].side is OrderSide.SELL and r3.closed == []
    r4 = led.on_signal(env("trend", Direction.FLAT, close=100.0, hours=72))
    (trade,) = r4.closed
    assert trade.opened_at == T0
    assert trade.pnl < 0  # mismo precio: solo pierde comisiones
    assert led.books["BTCUSDT"].qty == 0


def test_short_is_flat_in_spot_and_sizing_override():
    led = ReferenceLedger("x", T0, symbols=("BTCUSDT",), sizing="confidence")
    assert target_exposure(env("x", Direction.SHORT), "full") == 0.0
    assert target_exposure(env("x", confidence=0.4), "confidence") == pytest.approx(0.4)
    assert led.on_signal(env("x", Direction.SHORT)).fills == []


def test_capital_split_by_symbol_and_independent_books():
    led = ReferenceLedger("kronos", T0)
    led.on_signal(env(symbol="BTCUSDT", close=100.0))
    assert led.books["ETHUSDT"].cash == pytest.approx(50_000)
    assert led.books["BTCUSDT"].cash == pytest.approx(0, abs=1e-6)
    led.on_signal(env(symbol="BTCUSDT", close=200.0, hours=4))
    assert led.equity == pytest.approx(50_000 + 2 * 50_000 / (1 + FEE))


@pytest.mark.parametrize(
    ("envelope", "match"),
    [
        (env("trend"), "no de 'kronos'"),
        (env(symbol="SOLUSDT"), "no opera SOLUSDT"),
        (env(close=None), "no trae precio"),
    ],
)
def test_invalid_signals_raise(envelope, match):
    led = ReferenceLedger("kronos", T0)
    with pytest.raises(ValueError, match=match):
        led.on_signal(envelope)


def test_constructor_validation():
    with pytest.raises(ValueError):
        ReferenceLedger("k", T0, capital=0)
    with pytest.raises(ValueError):
        ReferenceLedger("k", T0, fee=0.5)
    with pytest.raises(ValueError):
        ReferenceLedger("k", T0, symbols=())
    with pytest.raises(ValueError, match="zona horaria"):
        ReferenceLedger("k", datetime(2026, 1, 1))


def test_state_roundtrip_with_open_position():
    led = ReferenceLedger("trend", T0)
    led.on_signal(env("trend", confidence=0.5, close=100.0))
    led.on_signal(env("trend", Direction.FLAT, close=105.0, hours=24))
    led.on_signal(env("trend", confidence=0.75, close=103.0, hours=48, symbol="ETHUSDT"))
    state = json.loads(json.dumps(led.to_state()))  # sobrevive a JSON (jsonb)
    back = ReferenceLedger.from_state("trend", state)
    assert back.equity == pytest.approx(led.equity)
    assert back.peak_equity == led.peak_equity
    assert back.daily == led.daily
    assert back.n_trades == led.n_trades == 1
    assert [t.ret for t in back.recent_returns] == [t.ret for t in led.recent_returns]
    assert back.books["ETHUSDT"].trip is not None
    assert back.last_signal_at == led.last_signal_at
    # sigue igual después de restaurar
    a = led.on_signal(env("trend", Direction.FLAT, close=110.0, hours=72, symbol="ETHUSDT"))
    b = back.on_signal(env("trend", Direction.FLAT, close=110.0, hours=72, symbol="ETHUSDT"))
    assert a.closed[0].pnl == pytest.approx(b.closed[0].pnl)


def test_metrics_comparable_with_lab_definitions():
    led = ReferenceLedger("kronos", T0, symbols=("BTCUSDT",))
    prices = [100, 101, 99, 103, 104, 102, 106]
    for day, p in enumerate(prices):
        led.on_signal(env(close=float(p), hours=24 * day))
    m = led.metrics()
    curve = led.equity_curve()
    assert curve[0] == 100_000 and len(curve) == len(prices) + 1
    rets = [b / a - 1 for a, b in itertools.pairwise(curve)]
    mean = sum(rets) / len(rets)
    std = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1))
    assert m["sharpe"] == pytest.approx(mean / std * math.sqrt(365))
    assert m["max_drawdown"] == pytest.approx(max_drawdown(curve))
    assert m["days"] == len(prices)
    assert m["cagr"] is None  # menos de 30 días
    assert m["trades"] == 0 and m["win_rate"] is None


def test_metric_helpers_edge_cases():
    assert daily_sharpe([1.0, 1.1]) is None
    assert daily_sharpe([1.0, 1.0, 1.0]) is None
    assert max_drawdown([]) == 0.0
    assert max_drawdown([100, 80, 120, 90]) == pytest.approx(0.25)


def test_cagr_after_30_days():
    led = ReferenceLedger("kronos", T0, symbols=("BTCUSDT",))
    for day in range(31):
        led.on_signal(env(close=100.0 + day, hours=24 * day))
    assert led.metrics()["cagr"] is not None
