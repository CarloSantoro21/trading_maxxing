"""CRUD de /profiles y /goals (#20)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

NEW_PROFILE = {"horizon": "medium", "risk_level": 3, "capital": "10000", "max_drawdown": "0.2"}


def test_crear_y_leer_mi_perfil(client, alice, fake_db):
    r = client.post("/profiles", json=NEW_PROFILE, headers=alice)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["capital"] == 10000
    assert body["instruments"] == ["BTCUSDT", "ETHUSDT"]
    # El user_id sale del token, nunca del cuerpo.
    assert body["user_id"] == "11111111-1111-4111-8111-111111111111"

    assert client.get("/profiles/me", headers=alice).json()["id"] == body["id"]
    assert client.get(f"/profiles/{body['id']}", headers=alice).status_code == 200
    assert [p["id"] for p in client.get("/profiles", headers=alice).json()] == [body["id"]]


def test_segundo_perfil_da_409(client, alice):
    assert client.post("/profiles", json=NEW_PROFILE, headers=alice).status_code == 201
    r = client.post("/profiles", json=NEW_PROFILE, headers=alice)
    assert r.status_code == 409


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("risk_level", 0),
        ("risk_level", 6),
        ("capital", "0"),
        ("capital", "-5"),
        ("max_drawdown", "0"),
        ("max_drawdown", "1.5"),
        ("horizon", "forever"),
        ("instruments", []),
        ("instruments", ["DOGEUSDT"]),
        ("instruments", ["BTCUSDT", "BTCUSDT"]),
        ("user_id", "11111111-1111-4111-8111-111111111111"),
    ],
)
def test_perfil_invalido_da_422(client, alice, campo, valor, fake_db):
    r = client.post("/profiles", json={**NEW_PROFILE, campo: valor}, headers=alice)
    assert r.status_code == 422
    assert any(campo in err["loc"] for err in r.json()["detail"])
    assert fake_db.tables["profiles"] == []


def test_perfil_me_sin_perfil(client, alice):
    r = client.get("/profiles/me", headers=alice)
    assert r.status_code == 404
    assert "POST /profiles" in r.json()["detail"]


def test_actualizar_y_borrar_perfil(client, alice, profile, fake_db):
    r = client.patch(f"/profiles/{profile['id']}", json={"risk_level": 5}, headers=alice)
    assert r.status_code == 200
    assert r.json()["risk_level"] == 5

    assert client.patch(f"/profiles/{profile['id']}", json={}, headers=alice).status_code == 422
    r = client.patch(f"/profiles/{profile['id']}", json={"capital": None}, headers=alice)
    assert r.status_code == 422

    assert client.delete(f"/profiles/{profile['id']}", headers=alice).status_code == 204
    assert fake_db.tables["profiles"] == []
    assert client.delete(f"/profiles/{profile['id']}", headers=alice).status_code == 404


def test_rls_perfil_ajeno_es_404(client, bob, profile):
    assert client.get(f"/profiles/{profile['id']}", headers=bob).status_code == 404
    assert (
        client.patch(f"/profiles/{profile['id']}", json={"risk_level": 1}, headers=bob).status_code
        == 404
    )
    assert client.delete(f"/profiles/{profile['id']}", headers=bob).status_code == 404
    assert client.get("/profiles", headers=bob).json() == []


def _future(days: int = 90) -> str:
    return (datetime.now(UTC).date() + timedelta(days=days)).isoformat()


def test_crud_de_metas(client, alice, profile):
    body = {"profile_id": profile["id"], "target_return": "0.15", "deadline": _future()}
    r = client.post("/goals", json=body, headers=alice)
    assert r.status_code == 201, r.text
    goal = r.json()
    assert goal["target_return"] == 0.15

    other = client.post("/goals", json={**body, "deadline": _future(10)}, headers=alice).json()
    listed = client.get("/goals", headers=alice).json()
    assert [g["id"] for g in listed] == [other["id"], goal["id"]]  # por fecha límite
    filtered = client.get("/goals", params={"profile_id": profile["id"]}, headers=alice)
    assert len(filtered.json()) == 2

    assert client.get(f"/goals/{goal['id']}", headers=alice).json()["id"] == goal["id"]
    r = client.patch(f"/goals/{goal['id']}", json={"target_return": "0.3"}, headers=alice)
    assert r.json()["target_return"] == 0.3
    assert client.delete(f"/goals/{goal['id']}", headers=alice).status_code == 204
    assert client.get(f"/goals/{goal['id']}", headers=alice).status_code == 404
    assert (
        client.patch(f"/goals/{goal['id']}", json={"target_return": "1"}, headers=alice).status_code
        == 404
    )
    assert client.delete(f"/goals/{goal['id']}", headers=alice).status_code == 404


@pytest.mark.parametrize(
    ("campo", "valor"),
    [("target_return", "0"), ("target_return", "-0.1"), ("deadline", "2000-01-01")],
)
def test_meta_invalida_da_422(client, alice, profile, campo, valor):
    body = {"profile_id": profile["id"], "target_return": "0.15", "deadline": _future()}
    r = client.post("/goals", json={**body, campo: valor}, headers=alice)
    assert r.status_code == 422
    assert any(campo in err["loc"] for err in r.json()["detail"])


def test_meta_en_perfil_ajeno_es_404(client, bob, profile):
    body = {"profile_id": profile["id"], "target_return": "0.15", "deadline": _future()}
    assert client.post("/goals", json=body, headers=bob).status_code == 404
