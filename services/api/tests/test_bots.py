"""Bots, comandos en tm:commands y paro de emergencia (#21)."""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from tm_api.commands import CommandBus, CommandPublishError, CommandType


class _BrokenRedis:
    async def xadd(self, *args, **kwargs):
        raise ConnectionError("redis caído")


@pytest.fixture
def broken_bus(client):
    """Sustituye el bus del app por uno cuyo Redis siempre falla."""
    bus = client.app.state.bus
    original = bus._redis
    bus._redis = _BrokenRedis()
    yield
    bus._redis = original


def _commands(reader):
    return [fields for _, fields in reader.xrange("tm:commands")]


def test_crear_listar_y_editar_bot(client, alice, profile):
    r = client.post("/bots", json={"name": "  trend-btc  "}, headers=alice)
    assert r.status_code == 201, r.text
    bot = r.json()
    assert bot["name"] == "trend-btc"
    assert bot["status"] == "stopped"
    assert bot["profile_id"] == profile["id"]

    assert [b["id"] for b in client.get("/bots", headers=alice).json()] == [bot["id"]]
    assert client.get("/bots", params={"status": "running"}, headers=alice).json() == []
    assert len(client.get("/bots", params={"profile_id": profile["id"]}, headers=alice).json()) == 1
    assert client.get(f"/bots/{bot['id']}", headers=alice).json()["id"] == bot["id"]

    cfg = {"trend": {"enabled": True, "weight": 0.5}}
    r = client.patch(f"/bots/{bot['id']}", json={"strategy_config": cfg}, headers=alice)
    assert r.json()["strategy_config"] == cfg


@pytest.mark.parametrize("body", [{"name": "   "}, {"name": "x" * 101}, {"name": "a", "x": 1}])
def test_bot_invalido(client, alice, profile, body):
    assert client.post("/bots", json=body, headers=alice).status_code == 422


def test_bot_sin_perfil(client, alice):
    assert client.post("/bots", json={"name": "a"}, headers=alice).status_code == 404


def test_start_stop_publican_comandos(client, alice, bot, redis_reader):
    r = client.post(f"/bots/{bot['id']}/start", headers=alice)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["command"] == "start"
    assert body["bot"]["status"] == "running"
    assert body["bot"]["started_at"] is not None

    # Arrancar dos veces es un conflicto.
    assert client.post(f"/bots/{bot['id']}/start", headers=alice).status_code == 409

    r = client.post(f"/bots/{bot['id']}/stop", headers=alice)
    assert r.status_code == 200
    assert r.json()["bot"]["status"] == "stopped"
    assert client.post(f"/bots/{bot['id']}/stop", headers=alice).status_code == 409

    cmds = _commands(redis_reader)
    assert [c["type"] for c in cmds] == ["start", "stop"]
    assert cmds[0]["bot_id"] == bot["id"]
    assert cmds[0]["user_id"] == "11111111-1111-4111-8111-111111111111"
    assert json.loads(cmds[0]["payload"]) == {"strategy_config": {}}
    entry_ids = [eid for eid, _ in redis_reader.xrange("tm:commands")]
    assert body["command_id"] == entry_ids[0]


def test_kill_y_rearranque(client, alice, bot, redis_reader):
    client.post(f"/bots/{bot['id']}/start", headers=alice)
    r = client.post(f"/bots/{bot['id']}/kill", json={"reason": "pánico"}, headers=alice)
    assert r.status_code == 200
    assert r.json()["bot"]["status"] == "killed"
    kill = _commands(redis_reader)[-1]
    assert kill["type"] == "kill"
    assert kill["reason"] == "pánico"
    assert json.loads(kill["payload"]) == {"close_positions": True}

    # Idempotente y sin cuerpo usa el motivo por defecto.
    r = client.post(f"/bots/{bot['id']}/kill", headers=alice)
    assert r.status_code == 200
    assert _commands(redis_reader)[-1]["reason"] == "paro de emergencia manual"

    # Un bot killed se puede volver a arrancar a mano; uno killed no se puede "detener".
    assert client.post(f"/bots/{bot['id']}/stop", headers=alice).status_code == 409
    assert (
        client.post(f"/bots/{bot['id']}/start", headers=alice).json()["bot"]["status"] == "running"
    )


def test_kill_all(client, alice, profile, fake_db, redis_reader):
    a = fake_db.seed("bots", profile_id=profile["id"], name="a", status="running")
    b = fake_db.seed("bots", profile_id=profile["id"], name="b", status="paused")
    fake_db.seed("bots", profile_id=profile["id"], name="c", status="killed")
    r = client.post("/bots/kill-all", headers=alice)
    assert r.status_code == 200
    killed = r.json()["killed"]
    assert {k["bot"]["id"] for k in killed} == {a["id"], b["id"]}
    assert all(k["bot"]["status"] == "killed" for k in killed)
    assert [c["type"] for c in _commands(redis_reader)] == ["kill", "kill"]


def test_bot_ajeno_es_404(client, bob, bot, redis_reader):
    for path in ("start", "stop", "kill"):
        assert client.post(f"/bots/{bot['id']}/{path}", headers=bob).status_code == 404
    assert client.get(f"/bots/{bot['id']}", headers=bob).status_code == 404
    assert client.patch(f"/bots/{bot['id']}", json={"name": "x"}, headers=bob).status_code == 404
    assert client.post("/bots/kill-all", headers=bob).json() == {"killed": []}
    assert _commands(redis_reader) == []


def test_borrar_bot(client, alice, bot, fake_db):
    client.post(f"/bots/{bot['id']}/start", headers=alice)
    assert client.delete(f"/bots/{bot['id']}", headers=alice).status_code == 409
    client.post(f"/bots/{bot['id']}/stop", headers=alice)
    assert client.delete(f"/bots/{bot['id']}", headers=alice).status_code == 204
    assert fake_db.tables["bots"] == []


def test_start_con_redis_caido_revierte(client, alice, bot, fake_db, broken_bus):
    r = client.post(f"/bots/{bot['id']}/start", headers=alice)
    assert r.status_code == 503
    assert "no se arrancó" in r.json()["detail"]
    assert fake_db.tables["bots"][0]["status"] == "stopped"
    assert fake_db.tables["bots"][0]["started_at"] is None


def test_stop_y_kill_con_redis_caido_conservan_estado(client, alice, profile, fake_db, broken_bus):
    running = fake_db.seed("bots", profile_id=profile["id"], name="a", status="running")
    assert client.post(f"/bots/{running['id']}/stop", headers=alice).status_code == 503
    assert running["status"] == "stopped"
    other = fake_db.seed("bots", profile_id=profile["id"], name="b", status="running")
    r = client.post(f"/bots/{other['id']}/kill", headers=alice)
    assert r.status_code == 503
    assert "killed" in r.json()["detail"]
    assert other["status"] == "killed"


def test_command_publish_error_envuelve_excepcion(settings):
    bus = CommandBus(_BrokenRedis(), settings)
    with pytest.raises(CommandPublishError, match="redis caído"):
        asyncio.run(
            bus.publish_command(CommandType.KILL, bot_id=uuid.uuid4(), user_id=uuid.uuid4())
        )
