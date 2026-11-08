"""Posición objetivo (#19): compras que agregan, ventas parciales y libros por bot.

Mismo BacktestEngine con el venue simulado de Binance Spot (comisión de 0.1 % por lado) que
`test_backtest_scenarios.py`. Las decisiones traen `target_quantity`; sin ese campo el motor
conserva el comportamiento del PR #69 (lo cubren las pruebas de escenarios).
"""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.model.enums import OrderStatus
from tm_engine.backtest import run_backtest
from tm_engine.decisions import ScheduledDecisionSource
from tm_shared.contracts import OrderSide

FLAT = [100, 100, 101, 102, 103, 103, 104, 104, 104, 104, 104]
UP_TO_TP = [100, 100, 101, 102, 103, 103, 104, 106, 108, 111, 112]


def _run(frame, envelopes, **kwargs):
    return run_backtest({"BTCUSDT": frame}, ScheduledDecisionSource(envelopes), **kwargs)


def _target(envelope, target: str, **update):
    return envelope.model_copy(update={"target_quantity": Decimal(target), **update})


def _by_role(result, role):
    return [o for o in result.orders if f"role:{role}" in o.tags]


def test_scale_in_adds_a_tranche_and_partial_sell_trims_lifo(make_frame, envelope):
    first = _target(envelope(quantity="1"), "1")
    second = _target(envelope(quantity="1", minute=3), "2")
    trim = _target(envelope(OrderSide.SELL, quantity="1", minute=5), "1")
    result = _run(make_frame(FLAT), [first, second, trim])

    assert result.rejections == []
    assert [(f.role, f.side, f.quantity) for f in result.fills] == [
        ("entry", OrderSide.BUY, Decimal("1")),
        ("entry", OrderSide.BUY, Decimal("1")),
        ("reduce", OrderSide.SELL, Decimal("1")),
    ]
    assert result.fills[-1].position_after == Decimal("1")
    position = result.sleeves["trend"]["positions"]["BTCUSDT"]
    assert Decimal(position["quantity"]) == Decimal("1")
    # el tramo más reciente se cancela; el primero sigue protegido por 1
    sl_first, sl_second = _by_role(result, "sl")
    tp_first, tp_second = _by_role(result, "tp")
    assert sl_second.status == OrderStatus.CANCELED
    assert tp_second.status == OrderStatus.CANCELED
    assert sl_first.is_open and tp_first.is_open
    assert sl_first.quantity.as_decimal() == Decimal("1")
    (reduce_order,) = _by_role(result, "reduce")
    assert reduce_order.is_reduce_only


def test_partial_sell_shrinks_protection_and_tp_closes_the_rest(make_frame, envelope):
    buy = _target(envelope(quantity="1"), "1")
    trim = _target(envelope(OrderSide.SELL, quantity="0.4", minute=4), "0.6")
    result = _run(make_frame(UP_TO_TP), [buy, trim])

    assert [(f.role, f.quantity) for f in result.fills] == [
        ("entry", Decimal("1")),
        ("reduce", Decimal("0.4")),
        ("tp", Decimal("0.6")),
    ]
    (sl,) = _by_role(result, "sl")
    (tp,) = _by_role(result, "tp")
    assert tp.quantity.as_decimal() == Decimal("0.6")
    assert sl.status == OrderStatus.CANCELED
    trend = result.sleeves["trend"]
    assert trend["positions"] == {}
    assert trend["closed_trades"] == 1
    # balance del venue = inicial + P&L realizado del sleeve (ya sin comisiones)
    assert result.final_balance_usdt == Decimal("100000") + Decimal(trend["realized_pnl"])


def test_target_zero_closes_everything(make_frame, envelope):
    first = _target(envelope(quantity="1"), "1")
    second = _target(envelope(quantity="0.5", minute=3), "1.5")
    close = _target(envelope(OrderSide.SELL, quantity="0.1", minute=5), "0")
    result = _run(make_frame(FLAT), [first, second, close])

    (exit_order,) = _by_role(result, "exit")
    assert exit_order.quantity.as_decimal() == Decimal("1.5")  # pide 0.1, pero el objetivo es 0
    assert all(
        o.status == OrderStatus.CANCELED for o in _by_role(result, "sl") + _by_role(result, "tp")
    )
    assert result.fills[-1].position_after == 0
    assert result.sleeves["trend"]["positions"] == {}


def test_sell_bigger_than_position_with_target_closes_everything(make_frame, envelope):
    buy = _target(envelope(quantity="1"), "1")
    sell = _target(envelope(OrderSide.SELL, quantity="3", minute=4), "0.5")
    result = _run(make_frame(FLAT), [buy, sell])
    assert [f.role for f in result.fills] == ["entry", "exit"]
    assert result.fills[-1].quantity == Decimal("1")


def test_scale_in_without_target_is_still_rejected(make_frame, envelope):
    result = _run(make_frame(FLAT), [envelope(), envelope(minute=3)])
    assert len(result.rejections) == 1
    assert "ya tiene una posición abierta" in result.rejections[0]


def test_books_are_kept_per_bot(make_frame, envelope):
    a = _target(envelope(quantity="1"), "1", bot_id="bot-a")
    b = _target(envelope(quantity="2"), "2", bot_id="bot-b")
    a_out = _target(envelope(OrderSide.SELL, quantity="1", minute=4), "0", bot_id="bot-a")
    result = _run(make_frame(FLAT), [a, b, a_out])

    assert set(result.sleeves) == {"bot-a/trend", "bot-b/trend"}
    assert result.sleeves["bot-a/trend"]["positions"] == {}
    held_b = result.sleeves["bot-b/trend"]["positions"]["BTCUSDT"]["quantity"]
    assert Decimal(held_b) == Decimal("2")
    assert {(f.bot_id, f.sleeve, f.role) for f in result.fills} == {
        ("bot-a", "trend", "entry"),
        ("bot-b", "trend", "entry"),
        ("bot-a", "trend", "exit"),
    }
    # la salida del bot A no toca la protección del bot B
    protections_b = [o for o in result.orders if "bot:bot-b" in o.tags and "role:sl" in o.tags]
    assert all(o.is_open for o in protections_b)


def test_shadow_decisions_never_reach_the_venue(make_frame, envelope):
    shadow = _target(envelope(quantity="1"), "1", shadow=True)
    result = _run(make_frame(FLAT), [shadow])
    assert result.orders == []
    assert result.fills == []
    assert "shadow" in result.rejections[0]


def test_partial_sell_that_rounds_to_zero_is_rejected(make_frame, envelope):
    buy = _target(envelope(quantity="1"), "1")
    dust = _target(envelope(OrderSide.SELL, quantity="0.000001", minute=4), "0.999999")
    result = _run(make_frame(FLAT), [buy, dust])
    assert any("redondea a 0" in r for r in result.rejections)
