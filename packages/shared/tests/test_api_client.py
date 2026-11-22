"""WorkerApiClient y HeartbeatReporter contra la API de Workers simulada con respx (sin red)."""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from enum import Enum

import httpx
import pytest
import respx
from tm_shared.api_client import (
    HeartbeatReporter,
    WorkerApiClient,
    WorkerApiError,
    _jsonable,
    api_configured,
)
from tm_shared.fake_api import FakeWorkerApi

URL = "https://tm-api.test"


def client(**kw) -> tuple[WorkerApiClient, list[float]]:
    waits: list[float] = []
    kw.setdefault("sleep", waits.append)
    return WorkerApiClient(URL, "tok", **kw), waits


@respx.mock
def test_headers_service_token_and_access():
    route = respx.get(f"{URL}/internal/settings").mock(
        return_value=httpx.Response(200, json={"news_enabled": False})
    )
    c, _ = client(access_client_id="id.access", access_client_secret="s3cr3t")
    assert c.get("/internal/settings") == {"news_enabled": False}
    headers = route.calls.last.request.headers
    assert headers["X-Service-Token"] == "tok"
    assert headers["CF-Access-Client-Id"] == "id.access"
    assert headers["CF-Access-Client-Secret"] == "s3cr3t"
    assert headers["User-Agent"].startswith("trading-maxxing-motor/")
    assert "authorization" not in headers


def test_only_one_access_header_is_ignored(caplog):
    with caplog.at_level(logging.WARNING):
        c, _ = client(access_client_id="solo-id")
    assert "CF-Access-Client-Id" not in c.headers
    assert "CF_ACCESS_CLIENT_SECRET" in caplog.text


def test_from_env_and_base_url_normalization():
    env = {
        "TM_API_URL": f"{URL}/internal/",
        "TM_SERVICE_TOKEN": "t",
        "CF_ACCESS_CLIENT_ID": "a",
        "CF_ACCESS_CLIENT_SECRET": "b",
        "TM_API_TIMEOUT_SECONDS": "3.5",
        "TM_API_RETRIES": "1",
    }
    c = WorkerApiClient.from_env(env)
    assert c.base_url == URL and c.timeout == 3.5 and c.retries == 1
    assert c.headers["CF-Access-Client-Secret"] == "b"
    c.close()
    assert api_configured(env) and not api_configured({"TM_API_URL": URL})
    with pytest.raises(ValueError, match="TM_API_URL"):
        WorkerApiClient.from_env({})
    with pytest.raises(ValueError, match="TM_SERVICE_TOKEN"):
        WorkerApiClient.from_env({"TM_API_URL": URL})


@respx.mock
def test_params_drop_none_and_encode_bools():
    route = respx.get(f"{URL}/internal/trades").mock(return_value=httpx.Response(200, json=[]))
    c, _ = client()
    assert c.get("internal/trades", {"open": True, "bot_id": None, "limit": 5}) == []
    params = route.calls.last.request.url.params
    assert params["open"] == "true" and params["limit"] == "5" and "bot_id" not in params


@respx.mock
def test_get_retries_5xx_with_exponential_backoff():
    route = respx.get(f"{URL}/internal/bots").mock(
        side_effect=[
            httpx.Response(503, json={"detail": "ocupado"}),
            httpx.ReadTimeout("lento"),
            httpx.Response(502, text="bad gateway"),
            httpx.Response(200, json=[{"id": "b"}]),
        ]
    )
    c, waits = client(retries=3, backoff=0.5)
    assert c.get("/internal/bots") == [{"id": "b"}]
    assert route.call_count == 4
    assert waits == [0.5, 1.0, 2.0]


@respx.mock
def test_backoff_is_capped_and_errors_surface_after_retries():
    respx.get(f"{URL}/internal/bots").mock(return_value=httpx.Response(500, json={"detail": "x"}))
    c, waits = client(retries=4, backoff=3, max_backoff=5)
    with pytest.raises(WorkerApiError) as info:
        c.get("/internal/bots")
    assert waits == [3, 5, 5, 5]
    assert info.value.status == 500 and info.value.detail == "x"


@respx.mock
def test_post_is_not_retried_on_5xx_or_read_timeout_but_is_on_connect_and_429():
    route = respx.post(f"{URL}/internal/trades").mock(
        return_value=httpx.Response(500, json={"detail": "x"})
    )
    c, _ = client(retries=2)
    with pytest.raises(WorkerApiError):
        c.post("/internal/trades", {"a": 1})
    assert route.call_count == 1
    route.side_effect = [httpx.ReadTimeout("lento")]
    with pytest.raises(WorkerApiError, match="ReadTimeout"):
        c.post("/internal/trades", {"a": 1})
    assert route.call_count == 2
    # la conexión ni siquiera se abrió, o el Worker pidió esperar: se puede repetir
    route.side_effect = [
        httpx.ConnectError("sin red"),
        httpx.Response(429, json={"detail": "lento"}),
        httpx.Response(201, json={"id": "t"}),
    ]
    assert c.post("/internal/trades", {"a": 1}) == {"id": "t"}
    # un POST marcado como idempotente sí se repite ante 5xx
    route.side_effect = [httpx.Response(503, json={}), httpx.Response(201, json={"ok": 1})]
    assert c.post("/internal/trades", {}, idempotent=True) == {"ok": 1}


@respx.mock
def test_contract_errors_carry_code_and_detail():
    respx.post(f"{URL}/internal/strategies").mock(
        return_value=httpx.Response(409, json={"detail": "ya existe", "code": "strategy_exists"})
    )
    respx.get(f"{URL}/internal/bots/x").mock(
        return_value=httpx.Response(404, json={"detail": "Bot no encontrado", "code": "not_found"})
    )
    c, _ = client()
    with pytest.raises(WorkerApiError) as info:
        c.post("/internal/strategies", {"name": "x"})
    err = info.value
    assert (err.status, err.code, err.detail) == (409, "strategy_exists", "ya existe")
    assert (err.method, err.path) == ("POST", "/internal/strategies")
    assert "[strategy_exists]" in str(err) and not err.not_found
    with pytest.raises(WorkerApiError) as info:
        c.get("/internal/bots/x")
    assert info.value.not_found


@respx.mock
def test_auth_errors_have_hints():
    respx.get(f"{URL}/internal/a").mock(
        return_value=httpx.Response(401, json={"detail": "token inválido"})
    )
    respx.get(f"{URL}/internal/b").mock(
        return_value=httpx.Response(302, headers={"Location": "https://x.cloudflareaccess.com"})
    )
    respx.get(f"{URL}/internal/c").mock(return_value=httpx.Response(403, text="<html>"))
    c, _ = client()
    with pytest.raises(WorkerApiError, match="TM_SERVICE_TOKEN"):
        c.get("/internal/a")
    with pytest.raises(WorkerApiError, match="Cloudflare Access"):
        c.get("/internal/b")
    with pytest.raises(WorkerApiError, match="CF_ACCESS_CLIENT_ID"):
        c.get("/internal/c")


@respx.mock
def test_empty_and_non_json_bodies():
    respx.post(f"{URL}/internal/x").mock(return_value=httpx.Response(204))
    respx.get(f"{URL}/internal/y").mock(return_value=httpx.Response(200, text="hola"))
    c, _ = client()
    assert c.post("/internal/x") is None
    with pytest.raises(WorkerApiError, match="no es JSON"):
        c.get("/internal/y")


def test_put_patch_and_context_manager():
    fake = FakeWorkerApi()
    with fake.client() as c:
        assert c.put("/internal/state/k", {"value": [1]})["value"] == [1]
        fake.add_strategy("trend")
        assert c.patch("/internal/strategies/trend", {"params": {"a": 1}})["params"] == {"a": 1}
    with WorkerApiClient(URL, "t") as own:
        assert own.base_url == URL  # cierra su propio httpx.Client al salir


def test_wrong_token_is_rejected_by_fake():
    fake = FakeWorkerApi(token="bueno")
    c = WorkerApiClient(
        fake.base_url, "malo", client=httpx.Client(transport=httpx.MockTransport(fake.handler))
    )
    with pytest.raises(WorkerApiError) as info:
        c.get("/internal/settings")
    assert info.value.status == 401


# --- latido ----------------------------------------------------------------------------------
def test_heartbeat_throttles_and_sends_contract_payload():
    fake = FakeWorkerApi()
    now = [0.0]
    hb = HeartbeatReporter(
        fake.client(),
        "engine",
        interval=30,
        version="1.2.3",
        host="mac",
        details=lambda: {"pnl": Decimal("1.5"), "mode": Enum("M", "a").a, "xs": (1, 2)},
        clock=lambda: now[0],
        wall_clock=lambda: "2026-11-22T00:00:00+00:00",
    )
    assert hb.maybe_beat() is True
    now[0] = 10
    assert hb.maybe_beat() is False  # todavía no toca
    now[0] = 31
    assert hb.maybe_beat("degraded") is True
    body = json.loads(fake.calls("POST", "/internal/heartbeat")[-1].content)
    assert body == {
        "component": "engine",
        "status": "degraded",
        "version": "1.2.3",
        "host": "mac",
        "started_at": "2026-11-22T00:00:00+00:00",
        "details": {"pnl": "1.5", "mode": "M.a", "xs": [1, 2]},
    }
    assert fake.heartbeats["engine"]["status"] == "degraded"


def test_heartbeat_never_raises():
    fake = FakeWorkerApi()
    fake.fail[("POST", "/internal/heartbeat")] = [httpx.Response(500, json={})] * 2

    def broken():
        raise RuntimeError("sin estado")

    hb = HeartbeatReporter(fake.client(), "agents", details=broken)
    assert hb.beat() is False and hb.failures == 1
    assert hb.beat() is True and hb.failures == 0
    assert fake.heartbeats["agents"]["details"] == {"details_error": "sin estado"}
    assert hb.version and hb.host


def test_jsonable():
    assert _jsonable({1: {Decimal("2")}, "n": None, "b": True}) == {
        "1": ["2"],
        "n": None,
        "b": True,
    }
