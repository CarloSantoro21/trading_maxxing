"""Persistencia del ledger de referencia (#57): memoria y PostgREST simulado con respx."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from uuid import uuid4

import httpx
import pytest
import respx
from tm_agents.reference.ledger import ReferenceTrade
from tm_agents.reference.store import (
    EquityPoint,
    InMemoryReferenceStore,
    PostgRESTReferenceStore,
    ReferenceStoreError,
    trade_row,
)
from tm_agents.strategy_stats import StrategyState

URL = "http://supabase.test"
SID = uuid4()
T0 = datetime(2026, 11, 1, tzinfo=UTC)


def a_trade(closed: datetime = T0) -> ReferenceTrade:
    return ReferenceTrade("trend", "BTCUSDT", T0, closed, 1.0, 100.0, 110.0, 0.21, 9.79, 0.0978)


def a_point(day: date = date(2026, 11, 1), equity: float = 100_000.0) -> EquityPoint:
    return EquityPoint(SID, T0, day, StrategyState.SHADOW, equity, equity, -1e-9, 1.5)


def test_rows_are_json_and_clamped():
    row = a_point().to_row()
    assert row["state"] == "shadow" and row["day"] == "2026-11-01"
    assert row["positions_value"] == 0.0 and row["drawdown"] == 1.0
    tr = trade_row(SID, T0, StrategyState.ACTIVE, a_trade())
    assert json.loads(json.dumps(tr))["closed_at"] == T0.isoformat()
    assert tr["state"] == "active" and tr["strategy_id"] == str(SID)


def test_in_memory_store_is_idempotent():
    store = InMemoryReferenceStore()
    row = trade_row(SID, T0, StrategyState.SHADOW, a_trade())
    store.save_trades([row, row])
    assert len(store.trades_for(SID)) == 1
    store.upsert_equity([a_point(equity=1.0)])
    store.upsert_equity([a_point(equity=2.0)])
    assert [r["equity"] for r in store.equity_for(SID)] == [2.0]


@respx.mock
def test_postgrest_store_posts_with_conflict_resolution():
    trades = respx.post(f"{URL}/rest/v1/reference_trades").mock(return_value=httpx.Response(201))
    equity = respx.post(f"{URL}/rest/v1/reference_equity").mock(return_value=httpx.Response(201))
    store = PostgRESTReferenceStore(URL, "eyJservice")
    store.save_trades([trade_row(SID, T0, StrategyState.SHADOW, a_trade())])
    store.upsert_equity([a_point()])
    store.save_trades([])  # sin filas no llama
    store.upsert_equity([])
    req = trades.calls.last.request
    assert req.url.params["on_conflict"] == "strategy_id,symbol,closed_at"
    assert req.headers["Prefer"] == "resolution=ignore-duplicates,return=minimal"
    assert req.headers["Authorization"] == "Bearer eyJservice"
    assert json.loads(req.content)[0]["symbol"] == "BTCUSDT"
    req = equity.calls.last.request
    assert req.url.params["on_conflict"] == "strategy_id,day"
    assert "merge-duplicates" in req.headers["Prefer"]
    assert trades.call_count == equity.call_count == 1
    store.close()


@respx.mock
def test_postgrest_store_errors():
    respx.post(f"{URL}/rest/v1/reference_equity").mock(
        return_value=httpx.Response(403, json={"message": "permission denied"})
    )
    respx.post(f"{URL}/rest/v1/reference_trades").mock(side_effect=httpx.ConnectError("down"))
    store = PostgRESTReferenceStore(URL, "sb_secret_x")
    with pytest.raises(ReferenceStoreError, match="403"):
        store.upsert_equity([a_point()])
    with pytest.raises(ReferenceStoreError, match="no se pudo llamar"):
        store.save_trades([trade_row(SID, T0, StrategyState.SHADOW, a_trade())])


def test_postgrest_store_from_env_and_validation():
    store = PostgRESTReferenceStore.from_env(
        {"SUPABASE_URL": URL, "SUPABASE_SERVICE_ROLE_KEY": "sb_secret_x"}
    )
    assert "Authorization" not in store._headers
    with pytest.raises(ValueError, match="URL"):
        PostgRESTReferenceStore("", "k")
    with pytest.raises(ValueError, match="llave"):
        PostgRESTReferenceStore(URL, "")
