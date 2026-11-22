"""WorkerApiTradeRecorder contra `/internal/trades` simulado con respx (sin red)."""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest
import respx
from tm_engine.recorder import FillRecord, MemoryFillRecorder, WorkerApiTradeRecorder
from tm_shared.api_client import WorkerApiClient, WorkerApiError
from tm_shared.contracts import OrderSide
from tm_shared.fake_api import FakeWorkerApi

URL = "https://tm-api.test"
ENDPOINT = f"{URL}/internal/trades"


def _fill(t0, side, role, qty, price, after, commission="0.1", pnl="-0.1", decision=None):
    return FillRecord(
        sleeve="trend",
        symbol="BTCUSDT",
        side=side,
        role=role,
        quantity=Decimal(qty),
        price=Decimal(price),
        commission=Decimal(commission),
        realized_pnl=Decimal(pnl),
        position_after=Decimal(after),
        client_order_id="O-1",
        ts=t0,
        decision=decision,
    )


@pytest.fixture
def recorder():
    client = WorkerApiClient(URL, "secret", sleep=lambda _s: None)
    yield WorkerApiTradeRecorder("bot-1", client=client)
    client.close()


@respx.mock
def test_open_partial_and_close_trade(recorder, envelope, t0):
    post = respx.post(ENDPOINT).mock(return_value=httpx.Response(201, json={"id": "t-1"}))
    patch = respx.patch(f"{ENDPOINT}/t-1").mock(return_value=httpx.Response(200, json={}))
    decision = envelope().decision
    recorder.on_fill(_fill(t0, OrderSide.BUY, "entry", "1", "100", "1", decision=decision))
    body = json.loads(post.calls.last.request.content)
    assert body["bot_id"] == "bot-1"
    assert body["rationale"]["sleeve"] == "trend"
    assert body["stop_loss"] == "95"
    assert post.calls.last.request.headers["X-Service-Token"] == "secret"

    recorder.on_fill(_fill(t0, OrderSide.BUY, "entry", "1", "102", "2"))
    assert json.loads(patch.calls.last.request.content)["entry_price"] == "101"

    recorder.on_fill(_fill(t0, OrderSide.SELL, "tp", "1", "110", "1", "0.11", "8.89"))
    # cierre parcial: la fila sigue abierta, pero su posición queda al día (#89)
    partial = json.loads(patch.calls.last.request.content)
    assert patch.call_count == 2 and "exit_price" not in partial
    assert partial["rationale"]["position"]["open_quantity"] == "1"
    assert partial["rationale"]["sleeve"] == "trend"
    recorder.on_fill(_fill(t0, OrderSide.SELL, "tp", "1", "112", "0", "0.11", "10.89"))
    closing = json.loads(patch.calls.last.request.content)
    assert closing["exit_price"] == "111"
    assert Decimal(closing["pnl"]) == Decimal("19.58")
    assert Decimal(closing["fees"]) == Decimal("0.42")


@respx.mock
def test_sell_without_open_trade_is_ignored(recorder, t0):
    route = respx.patch(url__startswith=ENDPOINT)
    recorder.on_fill(_fill(t0, OrderSide.SELL, "exit", "1", "100", "0"))
    assert not route.called


@respx.mock
def test_entry_without_decision_has_minimal_rationale(recorder, t0):
    post = respx.post(ENDPOINT).mock(return_value=httpx.Response(201, json={"id": "t-2"}))
    recorder.on_fill(_fill(t0, OrderSide.BUY, "entry", "1", "100", "1"))
    body = json.loads(post.calls.last.request.content)
    assert body["rationale"] == {
        "sleeve": "trend",
        "position": {
            "open_quantity": "1",
            "realized_pnl": "-0.1",
            "exit_notional": "0",
            "target_fraction": None,
        },
    }
    assert "stop_loss" not in body  # sin decisión no se manda (la API lo deja en null)


def test_requires_credentials():
    with pytest.raises(ValueError, match="TM_API_URL"):
        WorkerApiTradeRecorder("bot-1", env={})
    rec = WorkerApiTradeRecorder("bot-1", env={"TM_API_URL": URL, "TM_SERVICE_TOKEN": "t"})
    assert rec.client.base_url == URL


@respx.mock
def test_post_is_not_retried_on_5xx(recorder, t0):
    """Un POST que falla con 5xx no se repite: podría duplicar la fila."""
    post = respx.post(ENDPOINT).mock(return_value=httpx.Response(503, json={"detail": "x"}))
    with pytest.raises(WorkerApiError):
        recorder.on_fill(_fill(t0, OrderSide.BUY, "entry", "1", "100", "1"))
    assert post.call_count == 1


def test_open_positions_pages_through_open_buys(t0):
    api = FakeWorkerApi()
    for i in range(3):
        api.trades[f"t-{i}"] = {
            "id": f"t-{i}",
            "bot_id": f"bot-{i}",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quantity": 2.0,
            "entry_price": 100.0,
            "fees": 0.2,
            "rationale": {"sleeve": "trend", "position": {"open_quantity": "1.5"}},
            "opened_at": f"2026-01-0{i + 1}T00:00:00Z",
            "closed_at": None,
        }
    api.trades["closed"] = {**api.trades["t-0"], "id": "closed", "closed_at": "2026-02-01Z"}
    rec = WorkerApiTradeRecorder(client=api.client(), page=2)
    positions = rec.open_positions()
    assert [p.bot_id for p in positions] == ["bot-0", "bot-1", "bot-2"]
    assert positions[0].quantity == Decimal("1.5")
    gets = api.calls("GET", "/internal/trades")
    assert len(gets) == 2
    assert gets[0].url.params["open"] == "true" and gets[0].url.params["side"] == "buy"
    assert gets[1].url.params["offset"] == "2"


def test_memory_recorder(t0):
    memory = MemoryFillRecorder()
    memory.on_fill(_fill(t0, OrderSide.BUY, "entry", "1", "100", "1"))
    assert len(memory.fills) == 1


def test_fill_without_bot_and_unreadable_or_duplicate_open_rows(t0, caplog):
    api = FakeWorkerApi()
    rec = WorkerApiTradeRecorder(client=api.client())
    rec.on_fill(_fill(t0, OrderSide.BUY, "entry", "1", "100", "1"))  # sin bot: no se escribe
    assert api.trades == {} and "fill sin bot" in caplog.text
    base = {"symbol": "BTCUSDT", "side": "buy", "quantity": 1.0, "entry_price": 100.0}
    api.trades["a"] = {**base, "id": "a", "bot_id": "b", "opened_at": "1", "closed_at": None}
    api.trades["b"] = {**base, "id": "b", "bot_id": "b", "opened_at": "2", "closed_at": None}
    api.trades["c"] = {"id": "c", "side": "buy", "opened_at": "3", "closed_at": None}
    (pos,) = rec.open_positions()
    assert pos.bot_id == "b" and rec._open[("b", "default", "BTCUSDT")].id == "a"
    assert "ilegible" in caplog.text and "dos operaciones abiertas" in caplog.text
