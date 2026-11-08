"""GET /portfolio, GET /trades y POST /backtests (#21)."""

from __future__ import annotations

import json

import pytest


class _BrokenRedis:
    async def xadd(self, *args, **kwargs):
        raise ConnectionError("redis caído")


def _trade(fake_db, bot, **values):
    base = {
        "bot_id": bot["id"],
        "symbol": "BTCUSDT",
        "side": "buy",
        "quantity": 0.1,
        "entry_price": 50000,
        "opened_at": "2026-11-01T00:00:00+00:00",
    }
    return fake_db.seed("trades", **{**base, **values})


# --- portafolio ----------------------------------------------------------------


def test_portafolio_sin_equity_es_todo_usdt(client, alice, profile, bot):
    r = client.get("/portfolio", headers=alice)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["equity"] == p["cash"] == p["capital"] == 10000
    assert p["positions_value"] == 0
    assert p["total_return"] == 0
    assert p["sleeves"][0]["bot_id"] == bot["id"]
    assert p["sleeves"][0]["equity"] is None
    assert p["open_positions"] == []


def test_portafolio_sin_bots(client, alice, profile):
    p = client.get("/portfolio", params={"profile_id": profile["id"]}, headers=alice).json()
    assert p["sleeves"] == []
    assert p["equity"] == 10000


def test_portafolio_suma_sleeves(client, alice, profile, fake_db):
    trend = fake_db.seed("bots", profile_id=profile["id"], name="trend", status="running")
    kronos = fake_db.seed("bots", profile_id=profile["id"], name="kronos", status="running")
    fake_db.seed(
        "equity_snapshots",
        bot_id=trend["id"],
        ts="2026-11-01T00:00:00+00:00",
        equity=5000,
        cash=5000,
        positions_value=0,
        drawdown=0,
    )
    fake_db.seed(
        "equity_snapshots",
        bot_id=trend["id"],
        ts="2026-11-02T00:00:00+00:00",
        equity=5500,
        cash=500,
        positions_value=5000,
        drawdown=0.01,
    )
    fake_db.seed(
        "equity_snapshots",
        bot_id=kronos["id"],
        ts="2026-11-02T00:00:00+00:00",
        equity=4800,
        cash=4800,
        positions_value=0,
        drawdown=0.04,
    )
    _trade(fake_db, trend)  # abierta
    _trade(fake_db, trend, exit_price=51000, closed_at="2026-11-01T01:00:00+00:00", pnl=100)
    _trade(fake_db, kronos, exit_price=49000, closed_at="2026-11-01T01:00:00+00:00", pnl=-150)
    _trade(fake_db, kronos, exit_price=50000, closed_at="2026-11-01T02:00:00+00:00", pnl=None)

    p = client.get("/portfolio", headers=alice).json()
    assert p["equity"] == pytest.approx(10300)
    assert p["cash"] == pytest.approx(5300)
    assert p["positions_value"] == pytest.approx(5000)
    assert p["realized_pnl"] == pytest.approx(-50)
    assert p["total_return"] == pytest.approx(0.03)
    by_name = {s["name"]: s for s in p["sleeves"]}
    assert by_name["trend"]["equity"] == 5500  # la foto más reciente
    assert by_name["trend"]["open_trades"] == 1
    assert by_name["trend"]["realized_pnl"] == 100
    assert by_name["kronos"]["drawdown"] == 0.04
    assert len(p["open_positions"]) == 1


def test_portafolio_sin_perfil_y_ajeno(client, alice, bob, profile):
    assert client.get("/portfolio", headers=bob).status_code == 404
    r = client.get("/portfolio", params={"profile_id": profile["id"]}, headers=bob)
    assert r.status_code == 404


# --- trades --------------------------------------------------------------------


def test_trades_paginados_y_filtrados(client, alice, bob, profile, bot, fake_db):
    other = fake_db.seed("bots", profile_id=profile["id"], name="eth")
    for day in range(1, 6):
        _trade(fake_db, bot, opened_at=f"2026-11-0{day}T00:00:00+00:00")
    _trade(
        fake_db,
        other,
        symbol="ETHUSDT",
        entry_price=3000,
        exit_price=3100,
        closed_at="2026-11-06T00:00:00+00:00",
        opened_at="2026-11-05T12:00:00+00:00",
        pnl=10,
    )

    page = client.get("/trades", params={"limit": 2}, headers=alice).json()
    assert page["total"] == 6
    assert page["limit"] == 2
    assert len(page["items"]) == 2
    assert page["items"][0]["symbol"] == "ETHUSDT"  # más reciente primero

    page2 = client.get("/trades", params={"limit": 2, "offset": 4}, headers=alice).json()
    assert [t["opened_at"][:10] for t in page2["items"]] == ["2026-11-02", "2026-11-01"]

    q = {"bot_id": bot["id"], "symbol": "BTCUSDT", "state": "open"}
    assert client.get("/trades", params=q, headers=alice).json()["total"] == 5
    closed = client.get("/trades", params={"state": "closed"}, headers=alice).json()
    assert closed["total"] == 1
    assert closed["items"][0]["pnl"] == 10

    assert client.get("/trades", headers=bob).json()["total"] == 0


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 201}, {"offset": -1}, {"symbol": "DOGEUSDT"}, {"state": "x"}]
)
def test_trades_parametros_invalidos(client, alice, params):
    assert client.get("/trades", params=params, headers=alice).status_code == 422


# --- backtests -----------------------------------------------------------------

CONFIG = {"symbols": ["BTCUSDT"], "timeframe": "15m", "start": "2021-01-01", "end": "2024-12-31"}


def test_encolar_backtest(client, alice, profile, redis_reader, fake_db):
    r = client.post("/backtests", json={"config": {**CONFIG, "seed": 7}}, headers=alice)
    assert r.status_code == 202, r.text
    bt = r.json()
    assert bt["status"] == "queued"
    assert bt["profile_id"] == profile["id"]
    assert bt["config"]["mode"] == "walk_forward"
    assert bt["config"]["seed"] == 7  # parámetros extra de la estrategia se conservan

    [(_, job)] = redis_reader.xrange("tm:backtests")
    assert job["type"] == "backtest"
    assert job["backtest_id"] == bt["id"]
    assert json.loads(job["config"])["symbols"] == ["BTCUSDT"]

    assert client.get(f"/backtests/{bt['id']}", headers=alice).json()["id"] == bt["id"]
    assert [b["id"] for b in client.get("/backtests", headers=alice).json()] == [bt["id"]]
    assert client.get("/backtests", params={"status": "done"}, headers=alice).json() == []


@pytest.mark.parametrize(
    "config",
    [
        {**CONFIG, "end": "2020-01-01"},
        {**CONFIG, "symbols": []},
        {**CONFIG, "symbols": ["BTCUSDT", "BTCUSDT"]},
        {**CONFIG, "timeframe": "7m"},
        {**CONFIG, "mode": "yolo"},
        {**CONFIG, "initial_capital": "0"},
    ],
)
def test_backtest_invalido(client, alice, profile, config):
    assert client.post("/backtests", json={"config": config}, headers=alice).status_code == 422


def test_backtest_redis_caido_borra_la_fila(client, alice, profile, fake_db):
    client.app.state.bus._redis = _BrokenRedis()
    r = client.post("/backtests", json={"config": CONFIG}, headers=alice)
    assert r.status_code == 503
    assert fake_db.tables["backtests"] == []


def test_backtest_ajeno(client, bob, profile, fake_db):
    r = client.post("/backtests", json={"profile_id": profile["id"], "config": CONFIG}, headers=bob)
    assert r.status_code == 404
    bt = fake_db.seed("backtests", profile_id=profile["id"], config=CONFIG)
    assert client.get(f"/backtests/{bt['id']}", headers=bob).status_code == 404
