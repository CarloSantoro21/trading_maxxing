"""GET /goals/{id}/probability (#38): con la base en memoria y reportes en un directorio
temporal. Sin red."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient
from tm_api.main import create_app
from tm_api.settings import Settings

TODAY = datetime.now(UTC).date()


def _equity(n: int, mean: float, seed: int, start: date, capital: float = 100_000.0):
    rng = np.random.default_rng(seed)
    values = capital * np.cumprod(1 + rng.normal(mean, 0.02, n))
    return [(start + timedelta(days=i), float(v)) for i, v in enumerate(values)]


def _full_report(strategy: str = "trend", n: int = 400, mean: float = 0.002) -> dict:
    points = _equity(n, mean, 1, date(2021, 1, 1))
    return {
        "config": {"strategy": strategy},
        "aggregate": {"volatility": 0.35, "sharpe": 0.9},
        "montecarlo": {"max_drawdown": {"p95": 0.30}},
        "equity_daily": {"strategy": [[d.isoformat(), v] for d, v in points]},
    }


@pytest.fixture
def lab_dir(tmp_path):
    (tmp_path / "trend.json").write_text(json.dumps(_full_report()))
    return tmp_path


@pytest.fixture
def client(settings, fake_db, redis_server, lab_dir):
    import fakeredis
    from tm_api.commands import CommandBus

    s = Settings(**{**settings.__dict__, "lab_reports_dir": str(lab_dir)})
    bus = CommandBus(fakeredis.FakeAsyncRedis(server=redis_server), s)
    with TestClient(create_app(s, db=fake_db, bus=bus)) as c:
        yield c


@pytest.fixture
def goal(fake_db, profile):
    return fake_db.seed(
        "goals",
        profile_id=profile["id"],
        target_return=0.10,
        deadline=(TODAY + timedelta(days=365)).isoformat(),
    )


def _strategy(fake_db, name="trend", state="active", lab_report=None):
    return fake_db.seed("strategies", name=name, state=state, lab_report=lab_report)


def test_proyecta_con_el_laboratorio_si_no_hay_historia_de_referencia(client, alice, goal, fake_db):
    # Reporte plano en la base (como el seed): se usa el archivo del laboratorio.
    _strategy(fake_db, lab_report={"sharpe_oos": 0.82, "max_drawdown_p95": 0.24})
    _strategy(fake_db, name="kronos", state="shadow")
    r = client.get(f"/goals/{goal['id']}/probability", headers=alice)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["goal_id"] == goal["id"]
    assert body["capital"] == 10_000
    assert body["start_equity"] == 10_000  # sin fotos de equity: el capital
    assert body["target_equity"] == pytest.approx(11_000)
    assert body["days"] == 365
    assert 0 <= body["probability_at_deadline"] <= body["probability"] <= 1
    assert body["fan"][0]["day"] == TODAY.isoformat()
    assert len(body["fan"]) == 60
    (s,) = body["strategies"]  # kronos está en shadow: no se proyecta
    assert s["name"] == "trend"
    assert s["source"] == "lab_report" and s["report_source"] == "file"
    assert s["weight"] == pytest.approx(1.0) and 0 < s["exposure"] < 1
    assert body["notes"]


def test_usa_el_portafolio_de_referencia_y_el_equity_actual(client, alice, goal, fake_db, bot):
    strategy = _strategy(fake_db, lab_report=_full_report())
    started = "2026-01-01T00:00:00+00:00"
    for day, value in _equity(120, 0.001, 7, date(2026, 1, 1)):
        fake_db.seed(
            "reference_equity",
            strategy_id=strategy["id"],
            started_at=started,
            day=day.isoformat(),
            equity=value,
        )
    # Una corrida anterior no cuenta.
    fake_db.seed(
        "reference_equity",
        strategy_id=strategy["id"],
        started_at="2025-01-01T00:00:00+00:00",
        day="2025-06-01",
        equity=1.0,
    )
    fake_db.seed(
        "equity_snapshots", bot_id=bot["id"], ts="2026-11-01T00:00:00Z", equity=10_500, cash=10_500
    )
    fake_db.seed(
        "equity_snapshots", bot_id=bot["id"], ts="2026-11-07T00:00:00Z", equity=10_800, cash=10_800
    )
    body = client.get(f"/goals/{goal['id']}/probability", headers=alice).json()
    assert body["start_equity"] == 10_800
    (s,) = body["strategies"]
    assert s["source"] == "reference" and s["report_source"] == "db"
    assert s["observations"] == 119


def test_sin_estrategias_activas_la_probabilidad_es_cero(client, alice, goal):
    body = client.get(f"/goals/{goal['id']}/probability", headers=alice).json()
    assert body["probability"] == 0 and body["strategies"] == []
    assert body["n_simulations"] == 0


def test_estrategia_sin_reporte_ni_archivo(client, alice, goal, fake_db):
    _strategy(fake_db, name="nueva")
    (s,) = client.get(f"/goals/{goal['id']}/probability", headers=alice).json()["strategies"]
    assert s["source"] == "none" and s["report_source"] == "none" and s["excluded"]


def test_meta_ajena_da_404(client, bob, goal):
    assert client.get(f"/goals/{goal['id']}/probability", headers=bob).status_code == 404


def test_sin_token_da_401(client, goal):
    assert client.get(f"/goals/{goal['id']}/probability").status_code == 401


def test_fecha_vencida_da_422(client, alice, fake_db, profile):
    old = fake_db.seed(
        "goals", profile_id=profile["id"], target_return=0.1, deadline=TODAY.isoformat()
    )
    r = client.get(f"/goals/{old['id']}/probability", headers=alice)
    assert r.status_code == 422
    assert "ya pasó" in r.json()["detail"]
