"""BotRunner (#19): capa por usuario del orquestador."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from tm_agents.orchestrator.bot import (
    BotCommand,
    BotConfig,
    BotProfile,
    BotRunner,
    BotStatus,
    CommandType,
    VirtualBook,
)
from tm_agents.orchestrator.exposure import exposure_for_level
from tm_agents.orchestrator.streams import MemorySink
from tm_agents.strategy_stats import StrategyState
from tm_shared.contracts import OrderSide

NOW = datetime(2024, 3, 10, tzinfo=UTC)


def _qty(envelope) -> Decimal:
    return envelope.decision.quantity


def test_two_levels_same_signal_different_sizes(make_bot, make_event):
    conservative, sink_1, _ = make_bot("bot-1", risk_level=1)
    aggressive, sink_5, _ = make_bot("bot-5", risk_level=5)
    ev = make_event(1.0)
    (buy_1,) = conservative.on_event(ev)
    (buy_5,) = aggressive.on_event(ev)

    assert sink_1.items == [buy_1] and sink_5.items == [buy_5]
    assert buy_1.bot_id == "bot-1" and buy_5.bot_id == "bot-5"
    assert buy_1.sleeve == buy_5.sleeve == "trend"
    assert buy_1.decision.side is buy_5.decision.side is OrderSide.BUY
    # capital del sleeve en BTC = 10,000 * peso 1 / 2 símbolos; exposición según la curva
    for runner, envelope in ((conservative, buy_1), (aggressive, buy_5)):
        k = exposure_for_level(runner.config.profile.risk_level, 0.615, None or _trend_curve())
        expected = Decimal("5000") * Decimal(str(k)) / Decimal("50000")
        assert _qty(envelope) == expected.quantize(Decimal("0.00001"), rounding="ROUND_DOWN")
        assert envelope.target_quantity == _qty(envelope)
        assert envelope.decision.rationale.risk.approved
    assert _qty(buy_5) > 4 * _qty(buy_1)


def _trend_curve():
    from tm_agents.orchestrator.exposure import TREND_SENSITIVITY

    return TREND_SENSITIVITY


def test_trend_partial_buys_and_sells_by_channel(make_bot, make_event):
    runner, sink, _ = make_bot(risk_level=5)
    (first,) = runner.on_event(make_event(0.5, day=0))
    (second,) = runner.on_event(make_event(1.0, day=1))
    assert _qty(second) == _qty(first)  # cada canal mueve 1/4 del sleeve
    assert second.target_quantity == _qty(first) + _qty(second)
    held = second.target_quantity

    (trim,) = runner.on_event(make_event(0.75, day=2))
    assert trim.decision.side is OrderSide.SELL
    assert _qty(trim) == (held / 4).quantize(Decimal("0.00001"), rounding="ROUND_DOWN")
    assert trim.target_quantity == held - _qty(trim)

    assert runner.on_event(make_event(0.75, day=3)) == []  # sin cambio de confianza, no opera

    (trim_2,) = runner.on_event(make_event(0.25, day=4))
    assert trim_2.target_quantity > 0
    (close,) = runner.on_event(make_event(0.0, day=5))
    assert close.target_quantity == 0
    assert _qty(close) == trim_2.target_quantity
    assert runner.book.held() == []
    assert [e.decision.side for e in sink.items] == [OrderSide.BUY] * 2 + [OrderSide.SELL] * 3


def test_shadow_strategy_never_reaches_decisions(make_bot, make_event):
    runner, sink, shadow = make_bot()
    ev = make_event(1.0, state=StrategyState.SHADOW)
    out = runner.on_event(ev)
    out += runner.on_event(make_event(0.0, state=StrategyState.SHADOW, day=1))
    assert sink.items == []
    assert [e.decision.side for e in shadow.items] == [OrderSide.BUY, OrderSide.SELL]
    assert all(e.shadow for e in out)
    # se dimensiona como si fuera activa, sobre un libro aparte
    assert runner.book.equity() == Decimal("10000")
    assert runner.shadow_decisions == shadow.items


def test_news_veto_blocks_entries_but_not_exits(make_bot, make_event):
    runner, sink, _ = make_bot()
    assert runner.on_event(make_event(1.0, veto=True)) == []
    assert "veto por noticias" in runner.blocked[0].reasons[0]

    runner.on_event(make_event(0.5, day=1))
    assert runner.on_event(make_event(1.0, day=2, veto=True)) == []  # no agrega con veto
    (sell,) = runner.on_event(make_event(0.0, day=3, veto=True))
    assert sell.decision.side is OrderSide.SELL and sell.target_quantity == 0
    assert len(sink.items) == 2


def test_missing_simulation_blocks_entry(make_bot, make_event):
    runner, sink, _ = make_bot()
    ev = make_event(1.0, sim=None).model_copy(update={"sim_error": "historia insuficiente"})
    assert runner.on_event(ev) == []
    assert "historia insuficiente" in runner.blocked[0].reasons[0]
    assert sink.items == []


def test_negative_expected_value_is_blocked_by_risk(make_bot, make_event):
    from tm_shared.contracts import SimResult

    bad = SimResult(
        p_take_profit=0.1, p_stop_loss=0.8, p_open=0.1, expected_value=-0.5, n_paths=10, source="x"
    )
    runner, _sink, _ = make_bot()
    assert runner.on_event(make_event(1.0, sim=bad)) == []
    assert any("valor esperado" in r for r in runner.blocked[0].reasons)


def test_kill_closes_everything_and_stops(make_bot, make_event, make_snapshot):
    catalog = (make_snapshot(), make_snapshot("kronos", StrategyState.SHADOW))
    runner, sink, _ = make_bot()
    runner.on_event(make_event(1.0, catalog=catalog))
    runner.on_event(make_event(1.0, symbol="ETHUSDT", price="3000", catalog=catalog))
    shadow = make_event(1.0, state=StrategyState.SHADOW, strategy="kronos", catalog=catalog)
    assert runner.on_event(shadow)[0].shadow
    closes = runner.handle_command(BotCommand(CommandType.KILL, "bot-a", "pánico"), NOW)

    assert {(e.decision.symbol, e.target_quantity) for e in closes} == {
        ("BTCUSDT", 0),
        ("ETHUSDT", 0),
    }
    assert all(e.decision.side is OrderSide.SELL for e in closes)
    assert runner.status is BotStatus.KILLED and runner.kill_reason == "kill: pánico"
    assert runner.book.held() == [] and runner.shadow_book.positions == {}
    assert runner.on_event(make_event(1.0, day=1)) == []  # ya no opera
    assert sink.items[-2:] == closes


def test_commands_for_other_bots_are_ignored_and_start_resumes(make_bot, make_event):
    runner, _, _ = make_bot()
    assert runner.handle_command(BotCommand(CommandType.KILL, "otro"), NOW) == []
    runner.handle_command(BotCommand(CommandType.STOP, "bot-a"), NOW)
    assert not runner.running
    assert runner.on_event(make_event(1.0)) == []
    runner.handle_command(BotCommand(CommandType.START, "bot-a"), NOW)
    assert len(runner.on_event(make_event(1.0, day=1))) == 1
    runner.kill(NOW)
    runner.start()
    assert runner.running and runner.kill_reason is None


def test_drawdown_kill_switch(make_bot, make_event):
    runner, _sink, _ = make_bot(risk_level=5, max_drawdown=0.05)
    runner.on_event(make_event(1.0, price="50000"))
    out = runner.on_event(make_event(1.0, price="20000", day=1))
    assert runner.status is BotStatus.KILLED
    assert "kill switch" in runner.kill_reason
    assert len(out) == 1 and out[0].target_quantity == 0


def test_partial_sell_below_minimum_closes_or_blocks(make_bot, make_event):
    # Capital chico: un cuarto del sleeve ya no alcanza el mínimo de Binance (10 USDT).
    runner, _sink, _ = make_bot(risk_level=5, capital="100")
    runner.on_event(make_event(1.0, price="100"))
    held = runner.book.held()[0][2].quantity
    assert held * 100 < 40
    assert runner.on_event(make_event(0.9, day=1, price="100")) == []
    assert "mínimo de Binance" in runner.blocked[-1].reasons[0]
    (close,) = runner.on_event(make_event(0.25, day=2, price="100"))
    assert close.target_quantity == 0  # el resto quedaría bajo el mínimo: se cierra todo


def test_retired_strategy_sleeve_is_closed(make_bot, make_event, make_snapshot):
    runner, _sink, _ = make_bot()
    runner.on_event(make_event(1.0))
    other = make_event(1.0, strategy="kronos", catalog=(make_snapshot("kronos"),), day=1)
    out = runner.on_event(other)
    sells = [e for e in out if e.decision.side is OrderSide.SELL]
    assert len(sells) == 1 and sells[0].sleeve == "trend" and sells[0].target_quantity == 0


def test_disabled_strategy_and_foreign_symbols_are_skipped(make_event):
    sink = MemorySink()
    config = BotConfig(
        "bot-x",
        BotProfile(
            risk_level=3, capital=Decimal("1000"), max_drawdown=0.3, instruments=("ETHUSDT",)
        ),
        strategy_config={"trend": {"enabled": False}},
    )
    runner = BotRunner(config, sink)
    assert runner.on_event(make_event(1.0)) == []  # BTC no está en el perfil
    assert runner.on_event(make_event(1.0, symbol="ETHUSDT", price="3000")) == []
    assert sink.items == []


def test_strategy_without_p95_or_without_snapshot_gets_nothing(make_bot, make_event, make_snapshot):
    runner, sink, _ = make_bot()
    assert runner.on_event(make_event(1.0, catalog=(make_snapshot(p95=None),))) == []
    assert runner.on_event(make_event(1.0, catalog=(make_snapshot("kronos"),), day=1)) == []
    assert sink.items == []


def test_hard_stop_brackets_are_settled_in_the_book(make_bot, make_event):
    runner, sink, _ = make_bot(risk_level=5)
    kw = dict(strategy="kronos", hard_stop=True, stop_loss="49000", take_profit="52000")
    (buy,) = runner.on_event(make_event(1.0, **kw))
    assert buy.decision.stop_loss == Decimal("49000")
    # con stop real el RiskAgent recorta por riesgo por operación (2 % del equity en el nivel 5)
    assert _qty(buy) * (Decimal("50000") - Decimal("49000")) <= Decimal("200")
    assert runner.on_event(make_event(1.0, day=1, price="51000", **kw)) == []  # sigue abierta
    again = make_event(
        1.0,
        day=2,
        price="53000",
        strategy="kronos",
        hard_stop=True,
        stop_loss="52000",
        take_profit="56000",
    )
    assert runner.on_event(again) != []  # el TP ya se tocó: el sleeve vuelve a entrar
    assert len(sink.items) == 2 and all(e.decision.side is OrderSide.BUY for e in sink.items)


def test_risk_clip_advances_fraction_proportionally(make_event):
    from tm_agents.risk import RiskLimits

    sink = MemorySink()
    config = BotConfig("b", BotProfile(risk_level=5, capital=Decimal("10000"), max_drawdown=0.5))
    limits = RiskLimits.from_profile(5, 0.5, max_exposure=Decimal("0.1"))
    runner = BotRunner(config, sink, limits=limits)
    (buy,) = runner.on_event(make_event(1.0))
    assert _qty(buy) * 50000 <= 1000
    pos = runner.book.position("trend", "BTCUSDT")
    assert 0 < pos.fraction < 1


def test_reproducible_for_the_same_events(make_bot, make_event):
    events = [make_event(c, day=i) for i, c in enumerate([0.25, 0.75, 1.0, 0.5, 0.0, 1.0])]

    def run():
        runner, sink, _ = make_bot(risk_level=4)
        for ev in events:
            runner.on_event(ev)
        return [e.model_dump(mode="json") for e in sink.items], runner.snapshot()

    assert run() == run()
    decisions, snap = run()
    assert len(decisions) == 6 and snap["status"] == "running"


def test_virtual_book_accounting():
    book = VirtualBook(Decimal("1000"))
    book.mark("BTCUSDT", Decimal("100"), NOW)
    book.apply("trend", "BTCUSDT", OrderSide.BUY, Decimal("2"), Decimal("100"))
    book.apply("trend", "BTCUSDT", OrderSide.BUY, Decimal("2"), Decimal("110"))
    pos = book.position("trend", "BTCUSDT")
    assert pos.avg_price == Decimal("105")
    assert book.cash == Decimal("1000") - Decimal("420") - Decimal("0.42")
    book.apply("trend", "BTCUSDT", OrderSide.SELL, Decimal("10"), Decimal("100"))
    assert pos.quantity == 0 and book.portfolio().open_symbols == frozenset()
    assert book.trades_today == 3


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"risk_level": 0}, "risk_level"),
        ({"capital": Decimal("0")}, "capital"),
        ({"instruments": ()}, "instrumento"),
    ],
)
def test_profile_validation(kwargs, message):
    base = {"risk_level": 3, "capital": Decimal("100"), "max_drawdown": 0.2}
    with pytest.raises(ValueError, match=message):
        BotProfile(**{**base, **kwargs})


def test_command_from_fields():
    cmd = BotCommand.from_fields({"type": "kill", "bot_id": "b1", "reason": "x"}, "1-0")
    assert cmd == BotCommand(CommandType.KILL, "b1", "x", None, "1-0")
    with pytest.raises(ValueError):
        BotCommand.from_fields({"type": "explode", "bot_id": "b1"})
