"""Casos borde de AgenticStrategy: timer de sondeo, fallos del recorder y entradas rechazadas."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from nautilus_trader.model.currencies import BTC, USDT
from nautilus_trader.model.objects import Money
from tm_engine.backtest import build_engine
from tm_engine.decisions import ScheduledDecisionSource
from tm_engine.instruments import bar_type
from tm_engine.strategy import (
    AgenticStrategy,
    AgenticStrategyConfig,
    _commission_in_quote,
    _OpenTrade,
    _role,
)


class FailingRecorder:
    def __init__(self) -> None:
        self.calls = 0

    def on_fill(self, record) -> None:
        self.calls += 1
        raise RuntimeError("Supabase caído")


def test_timer_polling_and_recorder_failure_do_not_stop_engine(make_frame, envelope):
    frame = make_frame([100, 100, 101, 102, 104, 106, 108, 111, 112])
    engine = build_engine({"BTCUSDT": frame})
    recorder = FailingRecorder()
    strategy = AgenticStrategy(
        # Sin barras suscritas: solo el timer revisa la fuente.
        AgenticStrategyConfig(poll_interval_secs=30),
        source=ScheduledDecisionSource([envelope()]),
        recorder=recorder,
    )
    engine.add_strategy(strategy)
    try:
        engine.run()
        assert len(strategy.accepted) == 1
        assert recorder.calls == 2  # entrada y TP, aunque el recorder falle
        assert Decimal(strategy.sleeve_report()["trend"]["realized_pnl"]) > 0
    finally:
        engine.dispose()


def test_strategy_without_source_polls_nothing(make_frame):
    engine = build_engine({"BTCUSDT": make_frame([100, 101])})
    strategy = AgenticStrategy(AgenticStrategyConfig(bar_types=(str(bar_type("BTCUSDT")),)))
    engine.add_strategy(strategy)
    try:
        engine.run()
        assert strategy.poll_decisions() == 0
        assert strategy.accepted == []
    finally:
        engine.dispose()


def test_rejected_or_denied_entry_releases_sleeve(envelope):
    strategy = AgenticStrategy(AgenticStrategyConfig())
    env = envelope()
    for handler in (strategy.on_order_rejected, strategy.on_order_denied):
        strategy._open[("trend", "BTCUSDT")] = _OpenTrade(env, ["O-entry", "O-sl", "O-tp"])
        handler(SimpleNamespace(client_order_id=SimpleNamespace(value="O-sl"), reason="x"))
        assert ("trend", "BTCUSDT") in strategy._open  # un hijo no libera el sleeve
        handler(SimpleNamespace(client_order_id=SimpleNamespace(value="O-entry"), reason="x"))
        assert ("trend", "BTCUSDT") not in strategy._open
    assert [r.reason for r in strategy.rejections] == [
        "orden de entrada rechazada: x",
        "orden de entrada denegada: x",
    ]


def test_helpers():
    assert _role(["sleeve:trend"]) == "unknown"
    assert _role(None) == "unknown"
    price = Decimal("100")
    no_fee = SimpleNamespace(commission=None, currency=USDT)
    assert _commission_in_quote(no_fee, price) == 0
    in_quote = SimpleNamespace(commission=Money("0.1", USDT), currency=USDT)
    assert _commission_in_quote(in_quote, price) == Decimal("0.1")
    in_base = SimpleNamespace(commission=Money("0.001", BTC), currency=USDT)
    assert _commission_in_quote(in_base, price) == Decimal("0.1")
