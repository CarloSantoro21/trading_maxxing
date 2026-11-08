"""Prueba de integración opcional contra Supabase local.

Se omite salvo que estén definidas `SUPABASE_URL`, `SUPABASE_ANON_KEY` y
`SUPABASE_SERVICE_ROLE_KEY` (valores de `supabase status`). En CI no lo están.
Redis se simula con fakeredis; Auth, PostgREST y RLS son los reales.

    SUPABASE_URL=http://127.0.0.1:54321 SUPABASE_ANON_KEY=... SUPABASE_SERVICE_ROLE_KEY=... \\
      uv run pytest services/api -m integration
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import fakeredis
import httpx
import pytest
from fastapi.testclient import TestClient
from tm_api.commands import CommandBus
from tm_api.main import create_app
from tm_api.settings import Settings

URL = os.environ.get("SUPABASE_URL", "")
ANON = os.environ.get("SUPABASE_ANON_KEY", "")
SERVICE = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (URL and ANON and SERVICE),
        reason="requiere SUPABASE_URL, SUPABASE_ANON_KEY y SUPABASE_SERVICE_ROLE_KEY",
    ),
]


def _admin_headers() -> dict[str, str]:
    headers = {"apikey": SERVICE}
    if SERVICE.startswith("eyJ"):  # llave legacy (JWT); las sb_secret_ solo van en apikey
        headers["Authorization"] = f"Bearer {SERVICE}"
    return headers


@pytest.fixture
def user_token() -> Iterator[str]:
    email = f"api-it-{uuid.uuid4().hex[:8]}@test.local"
    password = "integration-pass-123"
    with httpx.Client(base_url=f"{URL.rstrip('/')}/auth/v1", timeout=10) as http:
        r = http.post(
            "/admin/users",
            headers=_admin_headers(),
            json={"email": email, "password": password, "email_confirm": True},
        )
        r.raise_for_status()
        user_id = r.json()["id"]
        try:
            r = http.post(
                "/token",
                params={"grant_type": "password"},
                headers={"apikey": ANON},
                json={"email": email, "password": password},
            )
            r.raise_for_status()
            yield r.json()["access_token"]
        finally:
            http.delete(f"/admin/users/{user_id}", headers=_admin_headers())


def test_flujo_completo_contra_supabase_local(user_token):
    settings = Settings.from_env()
    server = fakeredis.FakeServer()
    bus = CommandBus(fakeredis.FakeAsyncRedis(server=server), settings)
    headers = {"Authorization": f"Bearer {user_token}"}
    with TestClient(create_app(settings, bus=bus)) as c:
        body = {"risk_level": 2, "capital": "5000"}
        r = c.post("/profiles", json=body, headers=headers)
        assert r.status_code == 201, r.text
        profile = r.json()
        # El trigger deriva el drawdown del nivel 2 y la base pone los defaults.
        assert profile["max_drawdown"] == 0.15
        assert (profile["horizon"], profile["instruments"]) == ("medium", ["BTCUSDT", "ETHUSDT"])
        # Un usuario recién creado es member: no retira estrategias.
        r = c.post("/strategies/trend/retire", json={"reason": "prueba"}, headers=headers)
        assert r.status_code == 403
        assert c.get("/me", headers=headers).json()["role"] == "member"
        # El check de la base y la validación de Pydantic coinciden.
        assert c.post("/profiles", json=body, headers=headers).status_code == 409

        bot = c.post("/bots", json={"name": "it-bot"}, headers=headers).json()
        assert (
            c.post(f"/bots/{bot['id']}/start", headers=headers).json()["bot"]["status"] == "running"
        )
        assert (
            c.post(f"/bots/{bot['id']}/kill", headers=headers).json()["bot"]["status"] == "killed"
        )

        assert c.get("/trades", headers=headers).json()["total"] == 0
        assert c.get("/portfolio", headers=headers).json()["equity"] == 5000

        cfg = {"symbols": ["ETHUSDT"], "start": "2022-01-01", "end": "2023-01-01"}
        r = c.post("/backtests", json={"config": cfg}, headers=headers)
        assert r.status_code == 202, r.text

        assert c.delete(f"/profiles/{profile['id']}", headers=headers).status_code == 204

    reader = fakeredis.FakeRedis(server=server, decode_responses=True)
    assert [f["type"] for _, f in reader.xrange("tm:commands")] == ["start", "kill"]
    assert reader.xlen("tm:backtests") == 1
