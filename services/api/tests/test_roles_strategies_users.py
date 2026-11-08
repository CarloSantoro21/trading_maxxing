"""Roles (#67): /me, acciones de admin sobre estrategias y alta de usuarios."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import fakeredis
import httpx
import pytest
import respx
from fastapi import HTTPException
from fastapi.testclient import TestClient
from tm_api.commands import CommandBus
from tm_api.main import create_app
from tm_api.routers.strategies import shadow_days_clean
from tm_api.schemas import AppRole
from tm_api.settings import Settings
from tm_api.users import (
    NewUser,
    UserAdmin,
    UserAdminError,
    UserExistsError,
    generate_temporary_password,
    main,
    service_headers,
)

ALICE = "11111111-1111-4111-8111-111111111111"
SUPABASE = "http://supabase.test"
NEW_ID = "33333333-3333-4333-8333-333333333333"


# Hace 8 días: suficiente para promover desde shadow (mínimo 7 sin fallas).
EIGHT_DAYS_AGO = (datetime.now(UTC) - timedelta(days=8)).isoformat()


@pytest.fixture
def strategy(fake_db):
    def _make(name: str = "trend", state: str = "shadow", **extra):
        extra.setdefault("state_changed_at", EIGHT_DAYS_AGO)
        return fake_db.seed("strategies", name=name, state=state, **extra)

    return _make


# --- /me -----------------------------------------------------------------------------


def test_me_sin_fila_es_member(client, alice):
    r = client.get("/me", headers=alice)
    assert r.status_code == 200
    assert r.json() == {"id": ALICE, "email": f"{ALICE}@test.local", "role": "member"}


def test_me_admin(client, alice, make_admin, fake_db):
    make_admin()
    fake_db.seed("app_roles", user_id=str(uuid.uuid4()), role="member")  # rol de otro: no lo ve
    assert client.get("/me", headers=alice).json()["role"] == "admin"


def test_me_exige_token(client):
    assert client.get("/me").status_code == 401


# --- estrategias -----------------------------------------------------------------------


def test_cualquiera_lee_las_estrategias(client, bob, strategy):
    strategy("trend")
    strategy("kronos", "lab")
    r = client.get("/strategies", headers=bob)
    assert [s["name"] for s in r.json()] == ["kronos", "trend"]
    assert client.get("/strategies/kronos", headers=bob).json()["state"] == "lab"
    assert client.get("/strategies/nope", headers=bob).status_code == 404


@pytest.mark.parametrize("action", ["promote", "retire"])
def test_member_recibe_403(client, alice, strategy, fake_db, action):
    strategy()
    r = client.post(f"/strategies/trend/{action}", json={"reason": "porque sí"}, headers=alice)
    assert r.status_code == 403
    assert "admin" in r.json()["detail"]
    assert fake_db.tables["strategy_transitions"] == []
    assert fake_db.tables["strategies"][0]["state"] == "shadow"


def test_admin_promueve_y_retira(client, alice, make_admin, strategy, fake_db):
    make_admin()
    strategy()
    r = client.post(
        "/strategies/trend/promote", json={"reason": "  7 días en shadow  "}, headers=alice
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["from_state"], body["to_state"], body["actor"]) == ("shadow", "active", ALICE)
    assert body["reason"] == "7 días en shadow"
    assert body["strategy"]["state"] == "active"

    r = client.post("/strategies/trend/retire", json={"reason": "drawdown"}, headers=alice)
    assert r.json()["strategy"]["state"] == "retired"
    assert [t["actor"] for t in fake_db.tables["strategy_transitions"]] == [ALICE, ALICE]


def test_promover_desde_probation(client, alice, make_admin, strategy):
    make_admin()
    strategy(state="probation")
    r = client.post("/strategies/trend/promote", json={"reason": "se recuperó"}, headers=alice)
    assert r.json()["to_state"] == "active"


def test_promover_desde_shadow_exige_7_dias_limpios(client, alice, make_admin, strategy, fake_db):
    make_admin()
    three_days_ago = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    strategy(state_changed_at=three_days_ago)
    r = client.post("/strategies/trend/promote", json={"reason": "ya"}, headers=alice)
    assert r.status_code == 409
    assert "3.0 días" in r.json()["detail"]
    assert fake_db.tables["strategy_transitions"] == []


def test_una_falla_reciente_reinicia_los_dias_limpios(client, alice, make_admin, strategy):
    make_admin()
    one_day_ago = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    strategy(live_metrics={"shadow": {"clean_since": one_day_ago, "failures": 1}})
    r = client.post("/strategies/trend/promote", json={"reason": "ya"}, headers=alice)
    assert r.status_code == 409
    assert "1.0 días" in r.json()["detail"]


def test_shadow_days_clean():
    now = datetime(2026, 11, 8, tzinfo=UTC)
    base = {"state": "shadow", "state_changed_at": "2026-11-01T00:00:00Z", "live_metrics": {}}
    assert shadow_days_clean(base, now) == pytest.approx(7.0)
    # clean_since anterior a la entrada a shadow (corrida previa): manda state_changed_at.
    old = {**base, "live_metrics": {"shadow": {"clean_since": "2026-10-01T00:00:00+00:00"}}}
    assert shadow_days_clean(old, now) == pytest.approx(7.0)
    recent = {**base, "live_metrics": {"shadow": {"clean_since": "2026-11-07T12:00:00"}}}
    assert shadow_days_clean(recent, now) == pytest.approx(0.5)
    bad = {**base, "live_metrics": {"shadow": {"clean_since": "no-es-fecha"}}}
    assert shadow_days_clean(bad, now) == pytest.approx(7.0)
    assert shadow_days_clean({**base, "state_changed_at": None}, now) == 0.0
    future = {**base, "state_changed_at": datetime(2026, 11, 9, tzinfo=UTC)}
    assert shadow_days_clean(future, now) == 0.0
    assert shadow_days_clean({**base, "state": "active"}, now) is None


@pytest.mark.parametrize(
    ("state", "action"),
    [("lab", "promote"), ("active", "promote"), ("research", "promote"), ("retired", "retire")],
)
def test_transicion_fuera_de_estado_da_409(client, alice, make_admin, strategy, state, action):
    make_admin()
    strategy(state=state)
    r = client.post(f"/strategies/trend/{action}", json={"reason": "x"}, headers=alice)
    assert r.status_code == 409


def test_admin_estrategia_inexistente_404_y_motivo_vacio_422(client, alice, make_admin):
    make_admin()
    assert (
        client.post("/strategies/nope/retire", json={"reason": "x"}, headers=alice).status_code
        == 404
    )
    for body in ({"reason": "   "}, {}, {"reason": "x" * 1001}):
        assert client.post("/strategies/nope/retire", json=body, headers=alice).status_code == 422


def test_la_base_tambien_exige_el_rol(fake_db, strategy, token):
    """Si alguien se saltara la API, la política de la base (aquí, el doble) rechaza."""
    row = strategy()
    payload = {
        "strategy_id": row["id"],
        "from_state": "shadow",
        "to_state": "active",
        "actor": ALICE,
        "reason": "x",
    }
    with pytest.raises(HTTPException) as exc:
        asyncio.run(fake_db.insert("strategy_transitions", token(), payload))
    assert exc.value.status_code == 403


# --- alta de usuarios (API) ------------------------------------------------------------


class FakeUserAdmin:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.created: list[tuple[str, AppRole]] = []

    async def create_user(self, email: str, role: AppRole):
        if self.error:
            raise self.error
        self.created.append((email, role))
        return NewUser(uuid.UUID(NEW_ID), email, role, "TempPass1234abcd")


def _client_with(settings, fake_db, user_admin):
    bus = CommandBus(fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer()), settings)
    return TestClient(create_app(settings, db=fake_db, bus=bus, user_admin=user_admin))


def test_member_no_crea_usuarios(settings, fake_db, alice):
    admin = FakeUserAdmin()
    with _client_with(settings, fake_db, admin) as c:
        r = c.post("/admin/users", json={"email": "ana@equipo.dev"}, headers=alice)
    assert r.status_code == 403
    assert admin.created == []


def test_admin_crea_usuario(settings, fake_db, alice, make_admin):
    make_admin()
    admin = FakeUserAdmin()
    with _client_with(settings, fake_db, admin) as c:
        r = c.post(
            "/admin/users", json={"email": " Ana@Equipo.dev ", "role": "admin"}, headers=alice
        )
        assert r.status_code == 201, r.text
        assert r.json() == {
            "id": NEW_ID,
            "email": "ana@equipo.dev",
            "role": "admin",
            "temporary_password": "TempPass1234abcd",
        }
        for bad in ({"email": "no-es-correo"}, {"email": "x@y.z", "role": "owner"}, {"email": 5}):
            assert c.post("/admin/users", json=bad, headers=alice).status_code == 422
    assert admin.created == [("ana@equipo.dev", AppRole.ADMIN)]


@pytest.mark.parametrize(
    ("error", "code"),
    [(UserExistsError("ya existe"), 409), (UserAdminError("boom"), 502)],
)
def test_alta_propaga_errores(settings, fake_db, alice, make_admin, error, code):
    make_admin()
    with _client_with(settings, fake_db, FakeUserAdmin(error)) as c:
        r = c.post("/admin/users", json={"email": "ana@equipo.dev"}, headers=alice)
    assert r.status_code == code


def test_alta_sin_llave_de_servicio_da_503(client, alice, make_admin):
    make_admin()
    r = client.post("/admin/users", json={"email": "ana@equipo.dev"}, headers=alice)
    assert r.status_code == 503
    assert "SUPABASE_SERVICE_ROLE_KEY" in r.json()["detail"]


def test_la_api_arma_el_user_admin_con_la_llave(fake_db):
    settings = Settings(supabase_url=SUPABASE, supabase_service_role_key="sb_secret_x")
    bus = CommandBus(fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer()), settings)
    with TestClient(create_app(settings, db=fake_db, bus=bus)) as c:
        assert isinstance(c.app.state.user_admin, UserAdmin)


def test_settings_lee_la_llave_de_servicio(monkeypatch):
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "eyJservice")
    assert Settings.from_env().supabase_service_role_key == "eyJservice"
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_y")
    assert Settings.from_env().supabase_service_role_key == "sb_secret_y"


# --- UserAdmin contra Supabase simulado (respx) -----------------------------------------


def test_password_temporal():
    passwords = {generate_temporary_password() for _ in range(50)}
    assert len(passwords) == 50
    for p in passwords:
        assert len(p) == 16
        assert any(c.islower() for c in p) and any(c.isupper() for c in p)
        assert any(c.isdigit() for c in p)
        assert not set(p) & set("0O1lI")
    assert len(generate_temporary_password(24)) == 24
    with pytest.raises(ValueError):
        generate_temporary_password(8)


def test_service_headers():
    assert service_headers("eyJabc") == {"apikey": "eyJabc", "Authorization": "Bearer eyJabc"}
    assert service_headers("sb_secret_abc") == {"apikey": "sb_secret_abc"}


def _run_admin(fn):
    async def go():
        async with httpx.AsyncClient() as http:
            return await fn(UserAdmin(http, SUPABASE + "/", "eyJservice"))

    return asyncio.run(go())


@respx.mock
def test_create_user_crea_en_auth_y_guarda_el_rol():
    auth = respx.post(f"{SUPABASE}/auth/v1/admin/users").mock(
        return_value=httpx.Response(200, json={"id": NEW_ID, "email": "ana@equipo.dev"})
    )
    roles = respx.post(f"{SUPABASE}/rest/v1/app_roles").mock(return_value=httpx.Response(201))

    user = _run_admin(lambda a: a.create_user("ana@equipo.dev", AppRole.MEMBER))
    assert (str(user.id), user.email, user.role) == (NEW_ID, "ana@equipo.dev", AppRole.MEMBER)
    assert len(user.temporary_password) == 16

    sent = auth.calls.last.request
    assert sent.headers["apikey"] == "eyJservice"
    assert sent.headers["authorization"] == "Bearer eyJservice"
    body = json.loads(sent.content)
    assert body["email_confirm"] is True and body["password"] == user.temporary_password

    role_req = roles.calls.last.request
    assert role_req.url.params["on_conflict"] == "user_id"
    assert "merge-duplicates" in role_req.headers["prefer"]
    assert json.loads(role_req.content) == {"user_id": NEW_ID, "role": "member"}


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(422, json={"error_code": "email_exists", "msg": "exists"}),
        httpx.Response(
            422, json={"msg": "A user with this email address has already been registered"}
        ),
    ],
)
def test_create_user_correo_repetido(response):
    respx.post(f"{SUPABASE}/auth/v1/admin/users").mock(return_value=response)
    with pytest.raises(UserExistsError):
        _run_admin(lambda a: a.create_user("ana@equipo.dev", AppRole.MEMBER, password="x" * 12))


@respx.mock
def test_create_user_error_de_auth():
    respx.post(f"{SUPABASE}/auth/v1/admin/users").mock(
        return_value=httpx.Response(401, text="no autorizado")
    )
    with pytest.raises(UserAdminError, match="401"):
        _run_admin(lambda a: a.create_user("ana@equipo.dev", AppRole.ADMIN))


@respx.mock
def test_si_falla_el_rol_se_borra_el_usuario():
    respx.post(f"{SUPABASE}/auth/v1/admin/users").mock(
        return_value=httpx.Response(200, json={"id": NEW_ID})
    )
    respx.post(f"{SUPABASE}/rest/v1/app_roles").mock(
        return_value=httpx.Response(403, json={"message": "permission denied"})
    )
    deleted = respx.delete(f"{SUPABASE}/auth/v1/admin/users/{NEW_ID}").mock(
        return_value=httpx.Response(200)
    )
    with pytest.raises(UserAdminError, match="rol"):
        _run_admin(lambda a: a.create_user("ana@equipo.dev", AppRole.ADMIN))
    assert deleted.called


def test_user_admin_exige_llave():
    async def go():
        async with httpx.AsyncClient() as http:
            UserAdmin(http, SUPABASE, "")

    with pytest.raises(ValueError, match="service_role"):
        asyncio.run(go())


# --- script (make user) -------------------------------------------------------------------


def _transport(status_auth: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/v1/admin/users":
            if status_auth != 200:
                return httpx.Response(status_auth, json={"error_code": "email_exists"})
            return httpx.Response(200, json={"id": NEW_ID})
        if request.url.path == "/rest/v1/app_roles":
            return httpx.Response(201)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


ENV = {"SUPABASE_URL": SUPABASE, "SUPABASE_SERVICE_ROLE_KEY": "eyJservice"}


def test_script_crea_usuario(capsys):
    code = main(["--email", "Ana@Equipo.dev", "--role", "admin"], env=ENV, transport=_transport())
    out = capsys.readouterr().out
    assert code == 0
    assert "ana@equipo.dev (admin)" in out and NEW_ID in out
    assert "Contraseña temporal: " in out


def test_script_reporta_errores(capsys):
    assert main(["--email", "ana@equipo.dev"], env=ENV, transport=_transport(422)) == 1
    assert "ya existe" in capsys.readouterr().err
    assert main(["--email", "ana@equipo.dev"], env={"SUPABASE_URL": SUPABASE}) == 1
    assert "SUPABASE_SERVICE_ROLE_KEY" in capsys.readouterr().err


def test_script_valida_el_rol():
    with pytest.raises(SystemExit):
        main(["--email", "ana@equipo.dev", "--role", "owner"], env=ENV)
