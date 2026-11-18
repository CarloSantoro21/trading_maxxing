"""Pruebas del :class:`SignalAuditStore`: validación Pydantic, backend en memoria y PostgREST.

Sin red: el PostgREST se simula con ``httpx.MockTransport``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import ValidationError
from tm_agents.strategy_auditor import (
    InMemorySignalAuditStore,
    PostgRESTSignalAuditStore,
    SignalAudit,
    SignalAuditStoreError,
)

URL = "http://supabase.test"
REST = f"{URL}/rest/v1"
BOT = UUID("22222222-2222-2222-2222-222222222222")
TRADE = UUID("33333333-3333-3333-3333-333333333333")
CLOSE_TS = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


def an_audit(**overrides: Any) -> SignalAudit:
    """Factory con valores por defecto válidos; cada prueba solo sobreescribe lo suyo."""
    base: dict[str, Any] = {
        "bot_id": BOT,
        "trade_id": TRADE,
        "strategy": "trend",
        "symbol": "BTCUSDT",
        "close_ts": CLOSE_TS,
        "mode": "entry",
        "decision": "APPROVE",
        "confidence": 80,
        "rule_evaluations": {"volatility": "ok"},
        "input": {"candle": {"close": 100.0}},
        "output": {"notes": "ok"},
        "trace_id": "trace-xyz",
        "status": "ok",
        "latency_ms": 42,
        "model": "gpt-5",
    }
    base.update(overrides)
    return SignalAudit(**base)


# --- validación Pydantic -----------------------------------------------------------------
def test_audit_requires_valid_decision():
    with pytest.raises(ValidationError):
        an_audit(decision="MAYBE")


def test_audit_confidence_is_bounded():
    with pytest.raises(ValidationError):
        an_audit(confidence=150)
    with pytest.raises(ValidationError):
        an_audit(confidence=-1)


def test_audit_mode_is_entry_or_exit():
    with pytest.raises(ValidationError):
        an_audit(mode="rebalance")


def test_audit_status_is_bounded():
    with pytest.raises(ValidationError):
        an_audit(status="partial")


def test_audit_latency_non_negative():
    with pytest.raises(ValidationError):
        an_audit(latency_ms=-1)


def test_audit_unique_key_components():
    audit = an_audit()
    key = audit.unique_key()
    assert key == (
        str(BOT),
        "trend",
        "BTCUSDT",
        "entry",
        CLOSE_TS.isoformat(),
        str(TRADE),
    )
    # Sin trade_id el sexto componente es None (no la cadena "None").
    assert an_audit(trade_id=None).unique_key()[-1] is None


def test_audit_to_row_is_json_serialisable():
    row = an_audit().to_row()
    text = json.dumps(row)
    back = json.loads(text)
    assert back["bot_id"] == str(BOT)
    assert back["trade_id"] == str(TRADE)
    assert back["close_ts"] == CLOSE_TS.isoformat()
    assert back["mode"] == "entry" and back["decision"] == "APPROVE"
    assert back["confidence"] == 80 and back["status"] == "ok"
    assert back["rule_evaluations"] == {"volatility": "ok"}


# --- backend en memoria ------------------------------------------------------------------
def test_in_memory_store_roundtrip():
    store = InMemorySignalAuditStore()
    audit = an_audit()
    store.put(audit)
    got = store.get_by_key(BOT, "trend", "BTCUSDT", "entry", CLOSE_TS, TRADE)
    assert got == audit


def test_in_memory_store_dedup_replaces_by_unique_key():
    store = InMemorySignalAuditStore()
    first = an_audit(decision="APPROVE", confidence=60)
    second = an_audit(decision="REJECT", confidence=20, veto_reason="volatilidad alta")
    store.put(first)
    store.put(second)
    got = store.get_by_key(BOT, "trend", "BTCUSDT", "entry", CLOSE_TS, TRADE)
    assert got is not None and got.decision == "REJECT" and got.confidence == 20
    assert len(store.all()) == 1


def test_in_memory_store_distinguishes_by_mode_and_trade():
    store = InMemorySignalAuditStore()
    entry = an_audit(mode="entry", trade_id=None)
    exit_ = an_audit(mode="exit")
    store.put(entry)
    store.put(exit_)
    assert len(store.all()) == 2
    assert store.get_by_key(BOT, "trend", "BTCUSDT", "entry", CLOSE_TS, None) == entry
    assert store.get_by_key(BOT, "trend", "BTCUSDT", "exit", CLOSE_TS, TRADE) == exit_
    assert store.get_by_key(BOT, "trend", "BTCUSDT", "entry", CLOSE_TS, TRADE) is None


def test_in_memory_store_miss_returns_none():
    store = InMemorySignalAuditStore()
    assert store.get_by_key(BOT, "trend", "BTCUSDT", "entry", CLOSE_TS, TRADE) is None


# --- PostgREST con MockTransport ---------------------------------------------------------
def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_postgrest_store_put_upserts_with_merge_duplicates():
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["req"] = request
        return httpx.Response(201)

    store = PostgRESTSignalAuditStore(URL, "eyJservice", client=_client(handler))
    store.put(an_audit())

    req = captured["req"]
    assert req.method == "POST"
    assert str(req.url).startswith(f"{REST}/signal_audits")
    assert req.url.params["on_conflict"] == "bot_id,strategy,symbol,mode,close_ts,trade_id"
    assert req.headers["apikey"] == "eyJservice"
    assert req.headers["Authorization"] == "Bearer eyJservice"
    assert req.headers["Prefer"] == "resolution=merge-duplicates,return=minimal"
    body = json.loads(req.content)
    assert isinstance(body, list) and len(body) == 1
    row = body[0]
    assert row["bot_id"] == str(BOT) and row["trade_id"] == str(TRADE)
    assert row["mode"] == "entry" and row["decision"] == "APPROVE"
    assert row["confidence"] == 80 and row["rule_evaluations"] == {"volatility": "ok"}
    assert row["close_ts"] == CLOSE_TS.isoformat()


def test_postgrest_store_sb_secret_key_skips_authorization_header():
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["req"] = request
        return httpx.Response(201)

    store = PostgRESTSignalAuditStore(URL, "sb_secret_x", client=_client(handler))
    store.put(an_audit())
    req = captured["req"]
    assert req.headers["apikey"] == "sb_secret_x"
    assert "Authorization" not in req.headers


def test_postgrest_store_put_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "permission denied"})

    store = PostgRESTSignalAuditStore(URL, "eyJservice", client=_client(handler))
    with pytest.raises(SignalAuditStoreError, match="403"):
        store.put(an_audit())


def test_postgrest_store_put_raises_on_transport_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    store = PostgRESTSignalAuditStore(URL, "eyJservice", client=_client(handler))
    with pytest.raises(SignalAuditStoreError, match="no se pudo llamar"):
        store.put(an_audit())


def test_postgrest_store_get_by_key_filters_and_parses_row():
    captured: dict[str, httpx.Request] = {}
    stored_id = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        captured["req"] = request
        row = {
            "id": str(stored_id),
            "bot_id": str(BOT),
            "trade_id": str(TRADE),
            "strategy": "trend",
            "symbol": "BTCUSDT",
            "close_ts": CLOSE_TS.isoformat(),
            "mode": "entry",
            "decision": "APPROVE",
            "veto_reason": None,
            "confidence": 80,
            "rule_evaluations": {"volatility": "ok"},
            "input": {"candle": {"close": 100.0}},
            "output": {"notes": "ok"},
            "trace_id": "trace-xyz",
            "status": "ok",
            "latency_ms": 42,
            "model": "gpt-5",
            "created_at": "2026-11-18T12:00:01+00:00",
        }
        return httpx.Response(200, json=[row])

    store = PostgRESTSignalAuditStore(URL, "eyJservice", client=_client(handler))
    got = store.get_by_key(BOT, "trend", "BTCUSDT", "entry", CLOSE_TS, TRADE)

    req = captured["req"]
    assert req.method == "GET"
    assert req.url.params["bot_id"] == f"eq.{BOT}"
    assert req.url.params["strategy"] == "eq.trend"
    assert req.url.params["symbol"] == "eq.BTCUSDT"
    assert req.url.params["mode"] == "eq.entry"
    assert req.url.params["close_ts"] == f"eq.{CLOSE_TS.isoformat()}"
    assert req.url.params["trade_id"] == f"eq.{TRADE}"
    assert req.url.params["limit"] == "1"
    assert got is not None
    assert got.id == stored_id and got.bot_id == BOT and got.trade_id == TRADE
    assert got.decision == "APPROVE" and got.confidence == 80


def test_postgrest_store_get_by_key_handles_null_trade_id():
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["req"] = request
        return httpx.Response(200, json=[])

    store = PostgRESTSignalAuditStore(URL, "eyJservice", client=_client(handler))
    assert store.get_by_key(BOT, "trend", "BTCUSDT", "entry", CLOSE_TS, None) is None
    assert captured["req"].url.params["trade_id"] == "is.null"


def test_postgrest_store_from_env_and_validation():
    store = PostgRESTSignalAuditStore.from_env(
        {"SUPABASE_URL": URL, "SUPABASE_SERVICE_ROLE_KEY": "sb_secret_x"}
    )
    assert "Authorization" not in store._headers
    assert store._headers["apikey"] == "sb_secret_x"
    with pytest.raises(ValueError, match="URL"):
        PostgRESTSignalAuditStore("", "k")
    with pytest.raises(ValueError, match="llave"):
        PostgRESTSignalAuditStore(URL, "")
