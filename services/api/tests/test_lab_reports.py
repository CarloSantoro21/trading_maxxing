"""GET /lab/reports y /lab/reports/{name} (#24). Sin red."""

from __future__ import annotations

import json

import numpy as np
import pytest
from fastapi.testclient import TestClient
from tm_api import lab_reports
from tm_api.main import create_app
from tm_api.settings import Settings


def _report(n: int = 200) -> dict:
    rng = np.random.default_rng(0)
    eq = 100_000 * np.cumprod(1 + rng.normal(0.001, 0.02, n))
    days = [f"2021-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(n)]
    return {
        "config": {
            "strategy": "demo",
            "symbols": ["BTCUSDT"],
            "oos_start": "2021-01-01",
            "oos_end": "2022-01-01",
            "seed": 7,
        },
        "aggregate": {"sharpe": 1.1, "cagr": 0.3, "max_drawdown": 0.2},
        "buy_and_hold": {"sharpe": 0.7},
        "windows": [
            {
                "label": "2021-T1",
                "is_start": "2020-01-01",
                "params_label": "10/25",
                "is_sharpe": 2.0,
                "oos": {"total_return": 0.1, "sharpe": 1.5, "max_drawdown": 0.05},
                "buy_and_hold": {"total_return": 0.2},
                "trades": 4,
            }
        ],
        "montecarlo": {
            "n_simulations": 500,
            "block_size": 10,
            "horizon_days": 100,
            "max_drawdown": {"p95": 0.31},
        },
        "approval": {
            "approved": False,
            "criteria": {"max_drawdown_p95": 0.4},
            "checks": [{"name": "oos_sharpe", "passed": True}],
            "reasons": ["Drawdown alto"],
            "exposure_by_risk_level": {"1": 0.3},
        },
        "equity_daily": {
            "strategy": [[d, float(v)] for d, v in zip(days, eq, strict=True)],
            "buy_and_hold": [[d, 100_000.0] for d in days[:-1]],
        },
    }


@pytest.fixture
def lab_dir(tmp_path):
    (tmp_path / "demo.json").write_text(json.dumps(_report()))
    (tmp_path / "corto.json").write_text(
        json.dumps({"config": {}, "equity_daily": {"strategy": [["2021-01-01", 1.0]]}})
    )
    (tmp_path / "roto.json").write_text("{no es json")
    (tmp_path / "Mayus.json").write_text("{}")  # nombre inválido: se ignora
    return tmp_path


@pytest.fixture
def client(settings, fake_db, lab_dir):
    s = Settings(**{**settings.__dict__, "lab_reports_dir": str(lab_dir)})
    with TestClient(create_app(s, db=fake_db, bus=object())) as c:  # type: ignore[arg-type]
        yield c


def test_lista_los_reportes_validos(client, alice):
    r = client.get("/lab/reports", headers=alice)
    assert r.status_code == 200, r.text
    names = [x["name"] for x in r.json()]
    assert names == ["corto", "demo"]
    demo = r.json()[1]
    assert demo["strategy"] == "demo" and demo["max_drawdown_p95"] == 0.31
    assert demo["approved"] is False and demo["reasons"] == ["Drawdown alto"]


def test_detalle_con_curva_ventanas_e_histograma(client, alice):
    body = client.get("/lab/reports/demo", headers=alice).json()
    assert len(body["equity"]) == 200
    assert body["equity"][0]["buy_and_hold"] == 100_000.0
    assert body["equity"][-1]["buy_and_hold"] is None
    (w,) = body["windows"]
    assert w["oos_return"] == 0.1 and w["hold_return"] == 0.2 and w["trades"] == 4
    h = body["drawdown_histogram"]
    assert sum(b["count"] for b in h["bins"]) == 500
    assert len(h["bins"]) == lab_reports.HISTOGRAM_BINS
    assert h["p95"] == 0.31  # el del reporte
    assert 0 < h["p95_recomputed"] < 1
    assert (h["n_simulations"], h["block_size"], h["seed"], h["horizon_days"]) == (500, 10, 7, 100)
    assert h["limit"] == 0.4
    assert body["checks"] == [{"name": "oos_sharpe", "passed": True}]


def test_reporte_sin_curva_suficiente_no_trae_histograma(client, alice):
    body = client.get("/lab/reports/corto", headers=alice).json()
    assert body["drawdown_histogram"] is None
    assert body["strategy"] == "corto"


@pytest.mark.parametrize("name", ["nada", "Mayus", "..%2Fsecret"])
def test_reporte_inexistente_o_invalido(client, alice, name):
    assert client.get(f"/lab/reports/{name}", headers=alice).status_code in (404, 422)


def test_sin_token_da_401(client):
    assert client.get("/lab/reports").status_code == 401


def test_directorio_inexistente_da_lista_vacia(tmp_path):
    assert lab_reports.list_names(tmp_path / "no") == []
    assert lab_reports.reports_dir("") == lab_reports._REPO_REPORTS


def test_reportes_reales_del_repo_reproducen_su_p95():
    directory = lab_reports.reports_dir()
    if not (directory / "trend.json").is_file():
        pytest.skip("sin reportes del laboratorio en el repo")
    h = lab_reports.drawdown_histogram(directory, "trend")
    assert h is not None
    assert h["p95_recomputed"] == pytest.approx(h["p95"], abs=1e-6)


def test_cors_para_el_frontend(client):
    r = client.options(
        "/lab/reports",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_cors_origins_desde_el_entorno(monkeypatch):
    monkeypatch.setenv("API_CORS_ORIGINS", "https://app.example.dev/, http://x:1")
    monkeypatch.setenv("LAB_REPORTS_DIR", "/tmp/lab")
    s = Settings.from_env()
    assert s.cors_origins == ("https://app.example.dev", "http://x:1")
    assert s.lab_reports_dir == "/tmp/lab"
    monkeypatch.delenv("API_CORS_ORIGINS")
    assert Settings.from_env().cors_origins == Settings.cors_origins
