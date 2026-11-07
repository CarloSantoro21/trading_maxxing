"""Escenarios deterministas de AgenticStrategy sobre BacktestEngine (#14).

Velas sintéticas de 1m de BTCUSDT; comisión de 0.1 % por lado como en Binance Spot.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from nautilus_trader.model.enums import ContingencyType, OrderStatus, OrderType
from tm_engine.backtest import run_backtest
from tm_engine.decisions import ScheduledDecisionSource
from tm_shared.contracts import OrderSide

START = Decimal("100000")
UP_TO_TP = [100, 100, 101, 102, 104, 106, 108, 111, 112]
DOWN_TO_SL = [100, 100, 99, 97, 94, 93]


def _run(frame, envelopes, **kwargs):
    return run_backtest({"BTCUSDT": frame}, ScheduledDecisionSource(envelopes), **kwargs)


def _by_role(result, role):
    return [o for o in result.orders if f"role:{role}" in o.tags]


def test_buy_creates_bracket_market_entry_with_oco_children(make_frame, envelope):
    result = _run(make_frame(UP_TO_TP), [envelope()])
    (entry,) = _by_role(result, "entry")
    (sl,) = _by_role(result, "sl")
    (tp,) = _by_role(result, "tp")
    assert entry.order_type == OrderType.MARKET
    assert entry.contingency_type == ContingencyType.OTO
    assert not entry.is_reduce_only
    assert sl.order_type == OrderType.STOP_MARKET
    assert sl.trigger_price.as_decimal() == Decimal("95")
    assert tp.order_type == OrderType.LIMIT
    assert tp.price.as_decimal() == Decimal("110")
    for child in (sl, tp):
        assert child.contingency_type == ContingencyType.OUO
        assert child.is_reduce_only
        assert child.parent_order_id == entry.client_order_id
        assert "sleeve:trend" in child.tags


def test_take_profit_closes_and_cancels_stop(make_frame, envelope):
    result = _run(make_frame(UP_TO_TP), [envelope()])
    assert [(f.role, f.side) for f in result.fills] == [
        ("entry", OrderSide.BUY),
        ("tp", OrderSide.SELL),
    ]
    entry, tp = result.fills
    assert entry.price == Decimal("100")
    assert tp.price == Decimal("110")
    assert tp.position_after == 0
    (sl,) = _by_role(result, "sl")
    assert sl.status == OrderStatus.CANCELED
    # (110 - 100) * 1 - 0.1 % de comisión en cada lado (0.10 + 0.11)
    trend = result.sleeves["trend"]
    assert Decimal(trend["realized_pnl"]) == Decimal("9.79")
    assert Decimal(trend["commissions"]) == Decimal("0.21")
    assert trend["closed_trades"] == 1
    assert trend["positions"] == {}
    assert result.final_balance_usdt == START + Decimal("9.79")


def test_stop_loss_closes_and_cancels_take_profit(make_frame, envelope):
    result = _run(make_frame(DOWN_TO_SL), [envelope()])
    assert [f.role for f in result.fills] == ["entry", "sl"]
    sl_fill = result.fills[1]
    assert sl_fill.side is OrderSide.SELL
    assert sl_fill.price <= Decimal("95")
    (tp,) = _by_role(result, "tp")
    assert tp.status == OrderStatus.CANCELED
    pnl = Decimal(result.sleeves["trend"]["realized_pnl"])
    assert pnl < 0
    assert result.final_balance_usdt == START + pnl


def test_sell_decision_exits_reduce_only_and_cancels_bracket(make_frame, envelope):
    frame = make_frame([100, 100, 102, 104, 105, 105, 105])
    sell = envelope(OrderSide.SELL, quantity="5", minute=4)  # pide 5 pero el sleeve tiene 1
    result = _run(frame, [envelope(), sell])
    (exit_order,) = _by_role(result, "exit")
    assert exit_order.order_type == OrderType.MARKET
    assert exit_order.is_reduce_only
    assert exit_order.quantity.as_decimal() == Decimal("1")
    assert all(
        o.status == OrderStatus.CANCELED for o in _by_role(result, "sl") + _by_role(result, "tp")
    )
    assert [f.role for f in result.fills] == ["entry", "exit"]
    exit_fill = result.fills[1]
    assert exit_fill.position_after == 0
    expected = (exit_fill.price - Decimal("100")) - Decimal("0.1") - exit_fill.commission
    assert Decimal(result.sleeves["trend"]["realized_pnl"]) == expected
    assert result.final_balance_usdt == START + expected


def test_decision_rejected_by_risk_sends_no_orders(make_frame, envelope):
    result = _run(make_frame(UP_TO_TP), [envelope(approved=False)])
    assert result.orders == []
    assert result.fills == []
    assert result.rejections == ["bloqueada por RiskAgent: límite"]
    assert result.final_balance_usdt == START


@pytest.mark.parametrize(
    ("decisions", "reason"),
    [
        (lambda e: [e(OrderSide.SELL)], "sin posición en el sleeve"),
        (lambda e: [e(), e(minute=2)], "ya tiene una posición abierta"),
        (lambda e: [e(symbol="ETHUSDT")], "instrumento desconocido"),
    ],
)
def test_invalid_decisions_are_rejected(make_frame, envelope, decisions, reason):
    result = _run(make_frame(UP_TO_TP), decisions(envelope))
    assert len(result.rejections) == 1
    assert reason in result.rejections[0]


def test_buy_without_enough_cash_is_rejected(make_frame, envelope):
    frame = make_frame(UP_TO_TP)
    result = _run(frame, [envelope(), envelope(quantity="0.1", minute=3)], starting_usdt=50)
    assert len(result.rejections) == 1
    assert "saldo insuficiente" in result.rejections[0]
    # la segunda decisión sí alcanza y se ejecuta
    assert result.fills[0].role == "entry"
    assert result.fills[0].quantity == Decimal("0.1")


def test_pnl_is_attributed_per_sleeve(make_frame, envelope):
    frame = make_frame([100, 100, 101, 102, 104, 106, 108, 111, 112])
    trend = envelope(sleeve="trend", quantity="1", stop_loss="95", take_profit="110")
    kronos = envelope(sleeve="kronos", quantity="0.5", stop_loss="90", take_profit="105", minute=3)
    result = _run(frame, [trend, kronos])
    by_sleeve = {}
    for fill in result.fills:
        by_sleeve.setdefault(fill.sleeve, []).append(fill.role)
    assert by_sleeve == {"trend": ["entry", "tp"], "kronos": ["entry", "tp"]}
    k_entry = next(f for f in result.fills if f.sleeve == "kronos" and f.role == "entry")
    k_pnl = Decimal(result.sleeves["kronos"]["realized_pnl"])
    t_pnl = Decimal(result.sleeves["trend"]["realized_pnl"])
    assert t_pnl == Decimal("9.79")
    k_expected = (Decimal("105") - k_entry.price) * Decimal("0.5") - Decimal(
        result.sleeves["kronos"]["commissions"]
    )
    assert k_pnl == k_expected
    assert result.final_balance_usdt == START + t_pnl + k_pnl
