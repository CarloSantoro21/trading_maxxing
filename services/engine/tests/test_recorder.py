"""SupabaseTradeRecorder contra un PostgREST simulado con respx (sin red)."""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest
import respx
from tm_engine.recorder import FillRecord, MemoryFillRecorder, SupabaseTradeRecorder
from tm_shared.contracts import OrderSide

URL = "http://supabase.test"
ENDPOINT = f"{URL}/rest/v1/trades"


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
    with httpx.Client() as client:
        yield SupabaseTradeRecorder("bot-1", url=URL, service_key="secret", client=client)


@respx.mock
def test_open_partial_and_close_trade(recorder, envelope, t0):
    post = respx.post(ENDPOINT).mock(return_value=httpx.Response(201, json=[{"id": "t-1"}]))
    patch = respx.patch(ENDPOINT).mock(return_value=httpx.Response(200, json=[{"id": "t-1"}]))
    decision = envelope().decision
    recorder.on_fill(_fill(t0, OrderSide.BUY, "entry", "1", "100", "1", decision=decision))
    body = json.loads(post.calls.last.request.content)
    assert body["bot_id"] == "bot-1"
    assert body["rationale"]["sleeve"] == "trend"
    assert body["stop_loss"] == "95"
    assert post.calls.last.request.headers["Authorization"] == "Bearer secret"

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
    assert patch.calls.last.request.url.params["id"] == "eq.t-1"
    assert closing["exit_price"] == "111"
    assert Decimal(closing["pnl"]) == Decimal("19.58")
    assert Decimal(closing["fees"]) == Decimal("0.42")


@respx.mock
def test_sell_without_open_trade_is_ignored(recorder, t0):
    route = respx.patch(ENDPOINT)
    recorder.on_fill(_fill(t0, OrderSide.SELL, "exit", "1", "100", "0"))
    assert not route.called


@respx.mock
def test_entry_without_decision_has_minimal_rationale(recorder, t0):
    post = respx.post(ENDPOINT).mock(return_value=httpx.Response(201, json=[{"id": "t-2"}]))
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
    assert body["stop_loss"] is None


def test_requires_credentials(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    with pytest.raises(ValueError):
        SupabaseTradeRecorder("bot-1")


def test_memory_recorder(t0):
    memory = MemoryFillRecorder()
    memory.on_fill(_fill(t0, OrderSide.BUY, "entry", "1", "100", "1"))
    assert len(memory.fills) == 1
