"""Fixtures del servicio forecast: predictor simulado, sin red ni pesos de Kronos."""

import numpy as np
import pandas as pd
import pytest
import torch
from fastapi.testclient import TestClient
from tm_forecast.main import create_app
from tm_forecast.service import ForecastSettings, KronosForecastService


class FakePredictor:
    """Imita `KronosPredictor.predict_batch`: ruido de torch sobre el último cierre.

    Usa el generador global de torch, igual que el muestreo de Kronos, así que la semilla
    del servicio controla el resultado.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def predict_batch(self, df_list, x_timestamp_list, y_timestamp_list, pred_len, **kwargs):
        self.calls.append({"n": len(df_list), "pred_len": pred_len, **kwargs})
        out = []
        for df, y_ts in zip(df_list, y_timestamp_list, strict=True):
            assert len(y_ts) == pred_len
            last = float(df["close"].iloc[-1])
            noise = torch.randn(pred_len, 6).numpy().astype(np.float64)
            values = last * (1 + 0.01 * noise)
            cols = ["open", "high", "low", "close", "volume", "amount"]
            out.append(pd.DataFrame(values, columns=cols, index=y_ts))
        return out


def make_ohlcv(n: int = 64, start: str = "2025-01-01", freq: str = "15min") -> pd.DataFrame:
    ts = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    close = 100 + np.cumsum(np.random.default_rng(0).normal(0, 1, n))
    return pd.DataFrame(
        {
            "timestamp": ts,
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": np.full(n, 10.0),
        }
    )


def candles_json(n: int = 64) -> list[dict]:
    df = make_ohlcv(n)
    df["timestamp"] = df["timestamp"].map(lambda t: t.isoformat())
    return df.to_dict(orient="records")


@pytest.fixture
def fake_predictor() -> FakePredictor:
    return FakePredictor()


@pytest.fixture
def service(fake_predictor) -> KronosForecastService:
    return KronosForecastService(
        settings=ForecastSettings(device="cpu", preload=False), predictor=fake_predictor
    )


@pytest.fixture
def client(service) -> TestClient:
    with TestClient(create_app(service)) as c:
        yield c


@pytest.fixture
def ohlcv():
    """Fábrica de DataFrames OHLCV sintéticos."""
    return make_ohlcv


@pytest.fixture
def candles():
    """Fábrica de velas en formato JSON para el API."""
    return candles_json
