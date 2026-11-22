"""Persistencia del ledger de referencia (#57): memoria y API de Workers simulada (respx)."""

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
    ReferenceStoreError,
    WorkerApiReferenceStore,
    trade_row,
)
from tm_agents.strategy_stats import StrategyState
from tm_shared.api_client import WorkerApiClient

URL = "https://tm-api.test"
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


def _store(**kw) -> WorkerApiReferenceStore:
    return WorkerApiReferenceStore(WorkerApiClient(URL, "tok", sleep=lambda _s: None), **kw)


@respx.mock
def test_api_store_posts_trades_and_puts_equity():
    trades = respx.post(f"{URL}/internal/reference/trades").mock(
        return_value=httpx.Response(201, json={"received": 1, "inserted": 1})
    )
    equity = respx.put(f"{URL}/internal/reference/equity").mock(
        return_value=httpx.Response(200, json={"upserted": 1})
    )
    store = _store()
    store.save_trades([trade_row(SID, T0, StrategyState.SHADOW, a_trade())])
    store.upsert_equity([a_point()])
    store.save_trades([])  # sin filas no llama
    store.upsert_equity([])
    req = trades.calls.last.request
    assert req.headers["X-Service-Token"] == "tok"
    assert json.loads(req.content)["rows"][0]["symbol"] == "BTCUSDT"
    assert json.loads(equity.calls.last.request.content)["points"][0]["day"] == "2026-11-01"
    assert trades.call_count == equity.call_count == 1
    store.close()


@respx.mock
def test_api_store_chunks_and_retries_idempotent_writes():
    trades = respx.post(f"{URL}/internal/reference/trades").mock(
        side_effect=[
            httpx.Response(503, json={"detail": "D1 ocupado"}),
            httpx.Response(201, json={}),
            httpx.Response(201, json={}),
        ]
    )
    store = _store(chunk=2)
    rows = [
        trade_row(SID, T0, StrategyState.SHADOW, a_trade(datetime(2026, 11, d, tzinfo=UTC)))
        for d in (1, 2, 3)
    ]
    store.save_trades(rows)
    # 503 se reintenta (ignora duplicados): 2 lotes = 3 llamadas
    assert trades.call_count == 3
    assert len(json.loads(trades.calls.last.request.content)["rows"]) == 1


@respx.mock
def test_api_store_errors():
    respx.put(f"{URL}/internal/reference/equity").mock(
        return_value=httpx.Response(422, json={"detail": "check", "code": "check_violation"})
    )
    respx.post(f"{URL}/internal/reference/trades").mock(side_effect=httpx.ConnectError("down"))
    store = WorkerApiReferenceStore(WorkerApiClient(URL, "t", retries=0))
    with pytest.raises(ReferenceStoreError, match="422"):
        store.upsert_equity([a_point()])
    with pytest.raises(ReferenceStoreError, match="no se pudo llamar"):
        store.save_trades([trade_row(SID, T0, StrategyState.SHADOW, a_trade())])


def test_api_store_from_env_and_validation():
    store = WorkerApiReferenceStore.from_env({"TM_API_URL": URL, "TM_SERVICE_TOKEN": "t"})
    assert store.client.base_url == URL
    with pytest.raises(ValueError, match="TM_API_URL"):
        WorkerApiReferenceStore.from_env({})
