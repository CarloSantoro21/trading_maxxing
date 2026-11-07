"""Pruebas de KronosForecastService con el predictor simulado."""

from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
from tm_forecast import service as svc_mod
from tm_forecast.service import (
    ForecastSettings,
    KronosForecastService,
    future_timestamps,
    select_device,
    validate_ohlcv,
)


def test_predict_shape(service, ohlcv):
    f = service.predict(ohlcv(64), pred_len=24, samples=10, seed=1)
    assert f.paths.shape == (10, 24, 5)
    assert (f.samples, f.pred_len) == (10, 24)
    assert len(f.timestamps) == 24


def test_predict_replicates_series_per_sample(service, fake_predictor, ohlcv):
    service.predict(ohlcv(64), pred_len=6, samples=7)
    [call] = fake_predictor.calls
    assert call["n"] == 7
    assert call["sample_count"] == 1
    assert call["verbose"] is False


def test_predict_chunks_by_max_batch(fake_predictor, ohlcv):
    s = KronosForecastService(
        settings=ForecastSettings(device="cpu", max_batch=4), predictor=fake_predictor
    )
    f = s.predict(ohlcv(64), pred_len=3, samples=10)
    assert [c["n"] for c in fake_predictor.calls] == [4, 4, 2]
    assert f.paths.shape == (10, 3, 5)


def test_seed_is_reproducible(service, ohlcv):
    df = ohlcv(64)
    a = service.predict(df, pred_len=12, samples=5, seed=42).paths
    b = service.predict(df, pred_len=12, samples=5, seed=42).paths
    c = service.predict(df, pred_len=12, samples=5, seed=7).paths
    np.testing.assert_array_equal(a, b)
    assert not np.array_equal(a, c)


def test_trajectories_differ_between_samples(service, ohlcv):
    paths = service.predict(ohlcv(64), pred_len=4, samples=3, seed=0).paths
    assert not np.array_equal(paths[0], paths[1])


def test_context_is_truncated_to_max(fake_predictor, ohlcv):
    seen = []

    def spy(df_list, x_ts, y_ts, pred_len, **kw):
        seen.append(len(df_list[0]))
        return [pd.DataFrame(np.ones((pred_len, 6)), columns=[*"ohlcva"]) for _ in df_list]

    fake_predictor.predict_batch = spy
    s = KronosForecastService(
        settings=ForecastSettings(device="cpu", max_context=512), predictor=fake_predictor
    )
    with pytest.raises(KeyError):  # columnas falsas, solo interesa el tamaño de la entrada
        s.predict(ohlcv(600), pred_len=2, samples=1)
    assert seen == [512]


def test_batch_returns_one_forecast_per_series(service, ohlcv):
    out = service.predict_batch([ohlcv(64), ohlcv(64, start="2025-02-01")], 5, samples=3)
    assert [f.paths.shape for f in out] == [(3, 5, 5), (3, 5, 5)]
    assert out[1].timestamps.iloc[0] > out[0].timestamps.iloc[-1]


def test_batch_requires_same_length(service, ohlcv):
    with pytest.raises(ValueError, match="misma cantidad"):
        service.predict_batch([ohlcv(64), ohlcv(40)], 5, samples=2)


@pytest.mark.parametrize(
    ("kwargs", "msg"),
    [({"pred_len": 0}, "al menos 1"), ({"pred_len": 3, "samples": 0}, "al menos 1")],
)
def test_invalid_sizes(service, ohlcv, kwargs, msg):
    with pytest.raises(ValueError, match=msg):
        service.predict(ohlcv(64), **kwargs)


def test_empty_batch(service):
    with pytest.raises(ValueError, match="ninguna serie"):
        service.predict_batch([], 3)


@pytest.mark.parametrize("col", ["timestamp", "open", "close", "volume"])
def test_validate_missing_column(ohlcv, col):
    with pytest.raises(ValueError, match=f"Faltan columnas: {col}"):
        validate_ohlcv(ohlcv(64).drop(columns=[col]))


def test_validate_too_few_candles(ohlcv):
    with pytest.raises(ValueError, match="al menos 32"):
        validate_ohlcv(ohlcv(10))


def test_validate_duplicates_and_nulls(ohlcv):
    df = ohlcv(64)
    dup = pd.concat([df, df.tail(1)])
    with pytest.raises(ValueError, match="repetidos"):
        validate_ohlcv(dup)
    df.loc[3, "close"] = np.nan
    with pytest.raises(ValueError, match="nulos"):
        validate_ohlcv(df)


def test_validate_sorts_by_time(ohlcv):
    df = ohlcv(64).iloc[::-1]
    out = validate_ohlcv(df)
    assert out["timestamp"].is_monotonic_increasing


def test_future_timestamps_follow_frequency(ohlcv):
    ts = ohlcv(64)["timestamp"]
    fut = future_timestamps(ts, 3)
    assert list(fut) == [ts.iloc[-1] + pd.Timedelta(minutes=15) * i for i in (1, 2, 3)]


@pytest.mark.parametrize(
    ("override", "backends", "expected"),
    [
        (None, (True, True), "mps"),
        (None, (False, True), "cuda"),
        (None, (False, False), "cpu"),
        ("CPU", (True, True), "cpu"),
        ("cuda:1", (False, False), "cuda:1"),
    ],
)
def test_select_device(override, backends, expected):
    assert select_device(override, backends=lambda: backends) == expected


def test_select_device_rejects_unknown():
    with pytest.raises(ValueError, match="FORECAST_DEVICE"):
        select_device("tpu")


def test_select_device_with_real_torch():
    assert select_device() in ("mps", "cuda", "cpu")


def test_settings_from_env():
    s = ForecastSettings.from_env(
        {
            "FORECAST_MODEL": "NeoQuasar/Kronos-mini",
            "FORECAST_TOKENIZER": "NeoQuasar/Kronos-Tokenizer-2k",
            "FORECAST_DEVICE": "cpu",
            "FORECAST_MAX_BATCH": "64",
            "FORECAST_PRELOAD": "false",
        }
    )
    assert s == ForecastSettings(
        model="NeoQuasar/Kronos-mini",
        tokenizer="NeoQuasar/Kronos-Tokenizer-2k",
        device="cpu",
        max_batch=64,
        preload=False,
    )


def test_settings_defaults():
    s = ForecastSettings.from_env({})
    assert (s.model, s.tokenizer, s.device, s.preload, s.max_context) == (
        "NeoQuasar/Kronos-small",
        "NeoQuasar/Kronos-Tokenizer-base",
        None,
        True,
        512,
    )


def test_service_uses_env_device(monkeypatch):
    monkeypatch.setenv("FORECAST_DEVICE", "cpu")
    assert KronosForecastService().device == "cpu"


@pytest.fixture
def fake_kronos(monkeypatch):
    """Sustituye las clases de Kronos para probar load_model sin descargar pesos."""
    import tm_forecast.kronos as kronos_pkg

    tok_cls, model_cls, pred_cls = MagicMock(), MagicMock(), MagicMock()
    monkeypatch.setattr(kronos_pkg, "KronosTokenizer", tok_cls)
    monkeypatch.setattr(kronos_pkg, "Kronos", model_cls)
    monkeypatch.setattr(kronos_pkg, "KronosPredictor", pred_cls)
    return tok_cls, model_cls, pred_cls


def test_load_model_uses_settings(fake_kronos):
    tok_cls, model_cls, pred_cls = fake_kronos
    s = KronosForecastService(settings=ForecastSettings(device="cpu"))
    assert not s.loaded
    s.load_model()
    tok_cls.from_pretrained.assert_called_once_with("NeoQuasar/Kronos-Tokenizer-base")
    model_cls.from_pretrained.assert_called_once_with("NeoQuasar/Kronos-small")
    model_cls.from_pretrained.return_value.eval.assert_called_once()
    pred_cls.assert_called_once_with(
        model_cls.from_pretrained.return_value,
        tok_cls.from_pretrained.return_value,
        device="cpu",
        max_context=512,
    )
    assert s.loaded
    s.load_model()  # segunda llamada: no recarga
    assert model_cls.from_pretrained.call_count == 1
    s.load_model("NeoQuasar/Kronos-base")  # nombre explícito: recarga
    model_cls.from_pretrained.assert_called_with("NeoQuasar/Kronos-base")


def test_predict_loads_model_lazily(fake_kronos, fake_predictor, ohlcv):
    _, _, pred_cls = fake_kronos
    pred_cls.return_value = fake_predictor
    s = KronosForecastService(settings=ForecastSettings(device="cpu"))
    s.predict(ohlcv(64), pred_len=2, samples=2)
    assert s.predictor is fake_predictor
    assert pred_cls.call_count == 1


def test_seed_everything_seeds_numpy():
    svc_mod._seed_everything(3)
    a = np.random.rand()
    svc_mod._seed_everything(3)
    assert np.random.rand() == a
