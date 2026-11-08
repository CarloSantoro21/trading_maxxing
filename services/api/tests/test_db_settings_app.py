"""PostgrestClient (con respx), Settings y arranque del app sin inyecciones."""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx
from fastapi import HTTPException
from fastapi.testclient import TestClient
from tm_api.db import PostgrestClient, parse_content_range
from tm_api.main import create_app
from tm_api.settings import Settings

REST = "http://supabase.test/rest/v1"


def _run(coro):
    return asyncio.run(coro)


async def _with_client(fn):
    async with httpx.AsyncClient() as http:
        return await fn(PostgrestClient(http, REST + "/", "anon-key"))


@respx.mock
def test_select_reenvia_jwt_y_apikey():
    route = respx.get(f"{REST}/profiles").mock(return_value=httpx.Response(200, json=[{"id": 1}]))
    rows = _run(_with_client(lambda db: db.select("profiles", "user-jwt", {"id": "eq.1"})))
    assert rows == [{"id": 1}]
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer user-jwt"
    assert req.headers["apikey"] == "anon-key"
    assert req.url.params["id"] == "eq.1"


@respx.mock
def test_select_page_lee_content_range():
    route = respx.get(f"{REST}/trades").mock(
        return_value=httpx.Response(200, json=[{"id": 1}], headers={"Content-Range": "0-0/42"})
    )
    rows, total = _run(
        _with_client(lambda db: db.select_page("trades", "t", {}, limit=1, offset=0))
    )
    assert (rows, total) == ([{"id": 1}], 42)
    req = route.calls.last.request
    assert req.headers["prefer"] == "count=exact"
    assert req.url.params["limit"] == "1"


@respx.mock
def test_insert_update_delete():
    respx.post(f"{REST}/goals").mock(return_value=httpx.Response(201, json=[{"id": "g"}]))
    respx.patch(f"{REST}/goals").mock(return_value=httpx.Response(200, json=[{"id": "g"}]))
    respx.delete(f"{REST}/goals").mock(return_value=httpx.Response(200, json=[]))

    async def go(db):
        return (
            await db.insert("goals", "t", {"a": 1}),
            await db.update("goals", "t", {"id": "eq.g"}, {"a": 2}),
            await db.delete("goals", "t", {"id": "eq.g"}),
        )

    assert _run(_with_client(go)) == ({"id": "g"}, [{"id": "g"}], [])
    assert respx.calls[0].request.headers["prefer"] == "return=representation"


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (400, {"code": "23514", "message": "violates check"}, 422),
        (409, {"code": "23505", "message": "duplicate"}, 409),
        (403, {"code": "42501", "message": "rls"}, 403),
        (401, {"code": "PGRST301"}, 401),
        (401, {"code": "otro"}, 401),
        (500, {"code": "XX000"}, 502),
    ],
)
@respx.mock
def test_errores_de_postgrest(status, body, expected):
    respx.get(f"{REST}/profiles").mock(return_value=httpx.Response(status, json=body))
    with pytest.raises(HTTPException) as exc:
        _run(_with_client(lambda db: db.select("profiles", "t")))
    assert exc.value.status_code == expected


@respx.mock
def test_error_sin_json():
    respx.get(f"{REST}/profiles").mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(HTTPException) as exc:
        _run(_with_client(lambda db: db.select("profiles", "t")))
    assert exc.value.status_code == 502
    assert exc.value.detail["code"] is None


def test_parse_content_range():
    assert parse_content_range("0-49/123") == 123
    assert parse_content_range("*/0") == 0
    assert parse_content_range("0-1/*") == 0
    assert parse_content_range(None) == 0


def test_settings_desde_entorno(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "http://host.docker.internal:54321/")
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_x")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "s")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    s = Settings.from_env()
    assert s.rest_url == "http://host.docker.internal:54321/rest/v1"
    assert s.jwks_url == "http://host.docker.internal:54321/auth/v1/.well-known/jwks.json"
    assert s.supabase_anon_key == "sb_publishable_x"
    assert s.redis_url == "redis://redis:6379/0"
    assert s.commands_stream == "tm:commands"


def test_app_por_defecto_arranca_sin_red():
    """Sin inyecciones el lifespan crea PostgREST y Redis, pero no se conecta hasta usarlos."""
    app = create_app(Settings(supabase_jwt_secret="x" * 40))
    with TestClient(app) as c:
        assert c.get("/health").json()["service"] == "api"
        assert isinstance(c.app.state.db, PostgrestClient)


def test_openapi_documentado(client):
    spec = client.get("/openapi.json").json()
    tags = {t["name"] for t in spec["tags"]}
    assert {"profiles", "goals", "bots", "portfolio", "trades", "backtests"} <= tags
    paths = spec["paths"]
    for path in (
        "/bots/{bot_id}/start",
        "/bots/{bot_id}/stop",
        "/bots/{bot_id}/kill",
        "/portfolio",
        "/trades",
        "/backtests",
        "/profiles",
        "/goals",
    ):
        assert path in paths
    assert "examples" in spec["components"]["schemas"]["ProfileCreate"]
    assert spec["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"


def test_cors_origins_desde_el_entorno(monkeypatch):
    monkeypatch.delenv("API_CORS_ORIGINS", raising=False)
    assert Settings.from_env().cors_origins == ("http://localhost:3000", "http://127.0.0.1:3000")
    monkeypatch.setenv("API_CORS_ORIGINS", " https://tm.pages.dev/ , ,http://localhost:3000")
    assert Settings.from_env().cors_origins == ("https://tm.pages.dev", "http://localhost:3000")
    monkeypatch.setenv("API_CORS_ORIGINS", "")
    assert Settings.from_env().cors_origins == ()


def test_cors_permite_al_frontend(client):
    headers = {
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type",
    }
    r = client.options("/bots/kill-all", headers=headers)
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"
    r = client.options("/bots/kill-all", headers={**headers, "Origin": "https://malo.example"})
    assert "access-control-allow-origin" not in r.headers
