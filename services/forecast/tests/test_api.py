"""Contrato HTTP de /predict y /predict/batch con el predictor simulado."""

from fastapi.testclient import TestClient
from tm_forecast.main import create_app
from tm_forecast.service import ForecastSettings, KronosForecastService

OHLCV = {"timestamp", "open", "high", "low", "close", "volume"}


def test_health_reports_device_and_model(client):
    body = client.get("/health").json()
    assert body["service"] == "forecast"
    assert body["device"] == "cpu"
    assert body["model"] == "NeoQuasar/Kronos-small"
    assert body["tokenizer"] == "NeoQuasar/Kronos-Tokenizer-base"
    assert body["model_loaded"] is True


def test_predict_contract(client, candles):
    r = client.post("/predict", json={"candles": candles(64), "pred_len": 24, "samples": 5})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pred_len"] == 24
    assert body["samples"] == 5
    assert body["device"] == "cpu"
    assert len(body["timestamps"]) == 24
    assert len(body["trajectories"]) == 5
    assert all(len(path) == 24 for path in body["trajectories"])
    assert set(body["trajectories"][0][0]) == OHLCV
    assert body["trajectories"][0][0]["timestamp"] == body["timestamps"][0]


def test_predict_defaults_to_100_samples(client, candles, fake_predictor):
    r = client.post("/predict", json={"candles": candles(64)})
    assert r.status_code == 200
    body = r.json()
    assert (body["samples"], body["pred_len"], body["seed"]) == (100, 24, None)
    assert len(body["trajectories"]) == 100
    assert fake_predictor.calls[0]["n"] == 100


def test_predict_seed_is_reproducible(client, candles):
    payload = {"candles": candles(64), "pred_len": 4, "samples": 3, "seed": 123}
    a = client.post("/predict", json=payload).json()
    b = client.post("/predict", json=payload).json()
    assert a["seed"] == 123
    assert a["trajectories"] == b["trajectories"]


def test_predict_passes_sampling_params(client, candles, fake_predictor):
    payload = {"candles": candles(64), "pred_len": 2, "samples": 1, "temperature": 0.5}
    assert client.post("/predict", json=payload).status_code == 200
    call = fake_predictor.calls[0]
    assert (call["T"], call["top_p"], call["top_k"]) == (0.5, 0.9, 0)


def test_predict_missing_column_is_422(client, candles):
    data = candles(64)
    for c in data:
        del c["volume"]
    r = client.post("/predict", json={"candles": data})
    assert r.status_code == 422
    assert "volume" in r.text


def test_predict_too_few_candles_is_422(client, candles):
    r = client.post("/predict", json={"candles": candles(10)})
    assert r.status_code == 422


def test_predict_too_many_candles_is_422(client, candles):
    r = client.post("/predict", json={"candles": candles(513)})
    assert r.status_code == 422


def test_predict_invalid_params_is_422(client, candles):
    for bad in ({"samples": 0}, {"pred_len": 0}, {"samples": 501}, {"top_p": 0}):
        r = client.post("/predict", json={"candles": candles(64), **bad})
        assert r.status_code == 422, bad


def test_predict_service_error_is_422(client, candles):
    data = candles(64)
    data[-1]["timestamp"] = data[0]["timestamp"]  # timestamp repetido
    r = client.post("/predict", json={"candles": data})
    assert r.status_code == 422
    assert "repetidos" in r.json()["detail"]


def test_predict_batch_contract(client, candles):
    payload = {
        "series": [{"candles": candles(64)}, {"candles": candles(64)}],
        "pred_len": 6,
        "samples": 4,
        "seed": 1,
    }
    r = client.post("/predict/batch", json=payload)
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert len(results) == 2
    for res in results:
        assert len(res["trajectories"]) == 4
        assert all(len(p) == 6 for p in res["trajectories"])


def test_predict_batch_length_mismatch_is_422(client, candles):
    payload = {"series": [{"candles": candles(64)}, {"candles": candles(40)}]}
    r = client.post("/predict/batch", json=payload)
    assert r.status_code == 422
    assert "misma cantidad" in r.json()["detail"]


def test_predict_batch_empty_is_422(client):
    assert client.post("/predict/batch", json={"series": []}).status_code == 422


def test_lifespan_preloads_model_once(fake_predictor, monkeypatch):
    svc = KronosForecastService(settings=ForecastSettings(device="cpu", preload=True))
    calls = []

    def fake_load():
        calls.append(1)
        svc.predictor = fake_predictor

    monkeypatch.setattr(svc, "load_model", fake_load)
    with TestClient(create_app(svc)) as c:
        assert c.get("/health").json()["model_loaded"] is True
    assert calls == [1]


def test_lifespan_without_preload_is_lazy():
    svc = KronosForecastService(settings=ForecastSettings(device="cpu", preload=False))
    with TestClient(create_app(svc)) as c:
        assert c.get("/health").json()["model_loaded"] is False
