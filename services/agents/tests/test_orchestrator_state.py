"""Libro de cada bot tras un reinicio, desde la tabla ``trades`` (#89)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
import respx
from tm_agents.orchestrator.bot import BotCommand, BotConfig, BotProfile, BotStatus, CommandType
from tm_agents.orchestrator.state import (
    BotState,
    InMemoryTradeStateRepository,
    OpenPosition,
    PostgRESTTradeStateRepository,
    open_position_from_row,
    state_from_rows,
)
from tm_agents.orchestrator.streams import MemorySink
from tm_agents.orchestrator.supervisor import BotSupervisor, InMemoryBotRepository

URL = "http://supabase.test"
NOW = datetime(2024, 3, 10, tzinfo=UTC)


def _row(**kw):
    row = {
        "id": "t-1",
        "symbol": "BTCUSDT",
        "side": "buy",
        "quantity": "0.4",
        "entry_price": "50000",
        "stop_loss": "2500",
        "take_profit": "400000",
        "fees": "20",
        "pnl": None,
        "closed_at": None,
        "rationale": {"sleeve": "trend"},
    }
    row.update(kw)
    return row


def test_open_row_with_position_metadata():
    pos = open_position_from_row(
        _row(
            rationale={
                "sleeve": "trend",
                "position": {
                    "open_quantity": "0.3",
                    "realized_pnl": "150",
                    "target_fraction": 0.75,
                },
            }
        )
    )
    assert pos == OpenPosition(
        sleeve="trend",
        symbol="BTCUSDT",
        quantity=Decimal("0.3"),
        avg_price=Decimal("50000"),
        realized_pnl=Decimal("150"),
        stop_loss=Decimal("2500"),
        take_profit=Decimal("400000"),
        target_fraction=0.75,
    )


def test_legacy_row_without_position_metadata():
    # Filas anteriores al #89: abierta completa, P&L = -comisión, fracción de los weights.
    pos = open_position_from_row(
        _row(rationale={"sleeve": "kronos", "weights": {"target_fraction": 0.5}})
    )
    assert pos.quantity == Decimal("0.4") and pos.realized_pnl == Decimal("-20")
    assert pos.target_fraction == 0.5 and pos.sleeve == "kronos"
    assert open_position_from_row(_row(rationale={})).sleeve == "default"
    assert open_position_from_row(_row(rationale={"position": {"open_quantity": "0"}})) is None
    # nunca más de lo comprado
    big = open_position_from_row(_row(rationale={"position": {"open_quantity": "9"}}))
    assert big.quantity == Decimal("0.4")
    with pytest.raises(ValueError):
        open_position_from_row(_row(quantity="nan?"))


def test_state_from_rows_sums_closed_pnl_and_skips_bad_rows():
    rows = [
        _row(id="c1", closed_at="2024-03-02T00:00:00Z", pnl="300"),
        _row(id="c2", closed_at="2024-03-01T00:00:00Z", pnl="100"),
        _row(id="c3", closed_at="2024-03-03T00:00:00Z", pnl="-250"),
        _row(id="o1"),
        _row(id="o2"),  # duplicada: se usa la primera
        _row(id="o3", symbol="ETHUSDT", quantity="x"),  # ilegible
        _row(id="s1", side="sell"),
        _row(id="o4", rationale={"sleeve": "trend", "position": {"open_quantity": "0"}}),
    ]
    state = state_from_rows("bot-a", rows)
    assert state.closed_pnl == Decimal("150")
    assert state.peak_closed_pnl == Decimal("400")  # 100, 400, 150
    assert [(p.sleeve, p.symbol, p.quantity) for p in state.positions] == [
        ("trend", "BTCUSDT", Decimal("0.4"))
    ]
    assert state_from_rows("x", []) == BotState("x")


def test_runner_restore_rebuilds_cash_equity_and_peak(make_bot):
    runner, _sink, _shadow = make_bot("bot-a", capital="10000")
    state = BotState(
        "bot-a",
        positions=(
            OpenPosition("trend", "BTCUSDT", Decimal("0.05"), Decimal("50000"), Decimal("-2.5"),
                         Decimal("2500"), Decimal("400000"), 0.5),
            OpenPosition("kronos", "ETHUSDT", Decimal("1"), Decimal("2000"), Decimal("-2"),
                         Decimal("1900"), Decimal("2200"), None),
        ),
        closed_pnl=Decimal("500"),
        peak_closed_pnl=Decimal("800"),
    )  # fmt: skip
    assert runner.restore(state) == 2
    book = runner.book
    # 10000 + 500 - 2.5 - 2 - 2500 - 2000
    assert book.cash == Decimal("5995.5")
    assert book.equity() == Decimal("10495.5")  # a costo, sin precios todavía
    assert book.peak_equity == Decimal("10800")
    trend = book.position("trend", "BTCUSDT")
    kronos = book.position("kronos", "ETHUSDT")
    assert trend.fraction == 0.5 and trend.stop_loss is None  # catástrofe: no se liquida aquí
    assert kronos.fraction == 1.0  # sin fracción guardada: completa
    assert (kronos.stop_loss, kronos.take_profit) == (Decimal("1900"), Decimal("2200"))
    # el SL real de Kronos se sigue liquidando en el libro
    assert book.settle_brackets("ETHUSDT", Decimal("1890")) == [("kronos", Decimal("1900"))]
    with pytest.raises(ValueError):
        runner.restore(BotState("otro"))


def test_restored_runner_reduces_from_what_it_had(make_bot, make_event):
    runner, sink, _ = make_bot("bot-a", risk_level=5)
    qty = Decimal("0.08")
    runner.restore(
        BotState("bot-a", (OpenPosition("trend", "BTCUSDT", qty, Decimal("50000"),
                                        target_fraction=1.0),))
    )  # fmt: skip
    (trim,) = runner.on_event(make_event(0.5))
    assert trim.decision.side.value == "sell"
    assert trim.decision.quantity == qty / 2 and trim.target_quantity == qty / 2
    assert sink.items == [trim]


def _config(bot_id: str) -> BotConfig:
    return BotConfig(bot_id, BotProfile(risk_level=3, capital=Decimal("10000"), max_drawdown=0.5))


def test_supervisor_restores_on_sync_and_on_start():
    rows = {"bot-a": [_row()]}
    repo = InMemoryTradeStateRepository(rows)
    configs = [_config("bot-a"), _config("bot-b")]
    sup = BotSupervisor(InMemoryBotRepository(configs), MemorySink(), trades=repo)
    sup.sync()
    assert sup.runners["bot-a"].book.position("trend", "BTCUSDT").quantity == Decimal("0.4")
    assert sup.runners["bot-b"].book.held() == []
    # stop + start: el runner nuevo vuelve a leer trades (antes arrancaba vacío)
    sup.handle_command(BotCommand(CommandType.STOP, "bot-a"), NOW)
    assert "bot-a" not in sup.runners
    sup.handle_command(BotCommand(CommandType.START, "bot-a"), NOW)
    assert sup.runners["bot-a"].book.position("trend", "BTCUSDT").quantity == Decimal("0.4")


def test_supervisor_does_not_start_a_bot_without_its_state():
    class Down:
        calls = 0

        def load(self, bot_id):
            Down.calls += 1
            raise httpx.ConnectError("sin red")

    stopped = BotConfig("bot-c", _config("bot-c").profile, status=BotStatus.STOPPED)
    repo = InMemoryBotRepository([_config("bot-a"), stopped])
    sup = BotSupervisor(repo, MemorySink(), trades=Down())
    sup.sync()
    assert sup.runners == {}  # sin libro no opera: se reintenta en el siguiente sync
    sup.sync()
    assert Down.calls == 2
    assert sup.handle_command(BotCommand(CommandType.START, "bot-c"), NOW) == []
    assert sup.runners == {}


@respx.mock
def test_postgrest_repository_pages_and_filters_by_bot():
    endpoint = f"{URL}/rest/v1/trades"
    pages = [[_row(id="a"), _row(id="b", closed_at="2024-03-01", pnl="5")], [_row(id="c")]]
    route = respx.get(endpoint).mock(side_effect=[httpx.Response(200, json=p) for p in pages])
    with httpx.Client() as client:
        repo = PostgRESTTradeStateRepository(URL, "eyJkey", client=client, page=2)
        state = repo.load("bot-a")
    assert route.call_count == 2
    first, second = (c.request for c in route.calls)
    assert first.url.params["bot_id"] == "eq.bot-a"
    assert (first.url.params["offset"], second.url.params["offset"]) == ("0", "2")
    assert first.headers["Authorization"] == "Bearer eyJkey"
    assert state.closed_pnl == Decimal("5") and len(state.positions) == 1


def test_postgrest_repository_needs_env():
    with pytest.raises(ValueError):
        PostgRESTTradeStateRepository.from_env({})
    repo = PostgRESTTradeStateRepository.from_env(
        {"SUPABASE_URL": URL, "SUPABASE_SERVICE_ROLE_KEY": "sb_secret"}
    )
    assert "Authorization" not in repo._headers
