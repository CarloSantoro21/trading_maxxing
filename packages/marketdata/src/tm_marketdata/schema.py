"""Esquema común de las velas y normalización de los formatos de Binance."""

from __future__ import annotations

import numpy as np
import pandas as pd

INDEX = "open_time"
OHLCV = ["open", "high", "low", "close", "volume"]
# Columnas extra que se guardan en Parquet; `quote_volume` es el "amount" que usa Kronos.
EXTRA = ["quote_volume", "trades"]
COLUMNS = OHLCV + EXTRA

# Orden de los campos en la REST (/api/v3/klines) y en los CSV de data.binance.vision.
RAW_FIELDS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trades",
    "taker_buy_base",
    "taker_buy_quote",
    "ignore",
]

# Por arriba de este valor un timestamp ya no son milisegundos sino microsegundos
# (1e14 ms es el año 5138; 1e14 µs es 1973). Desde 2025 Binance publica algunos
# archivos de spot en microsegundos.
_MICROSECONDS_THRESHOLD = 10**14


def empty_frame() -> pd.DataFrame:
    """DataFrame vacío con el esquema correcto."""
    index = pd.DatetimeIndex([], tz="UTC", name=INDEX)
    df = pd.DataFrame({c: pd.Series(dtype="float64") for c in OHLCV}, index=index)
    df["quote_volume"] = pd.Series(dtype="float64")
    df["trades"] = pd.Series(dtype="int64")
    return df


def to_milliseconds(values: np.ndarray) -> np.ndarray:
    """Normaliza timestamps a milisegundos, detectando fila por fila los microsegundos."""
    values = np.asarray(values, dtype="int64")
    return np.where(values >= _MICROSECONDS_THRESHOLD, values // 1_000, values)


def normalize_klines(raw: pd.DataFrame | list[list]) -> pd.DataFrame:
    """Convierte velas en formato Binance (lista o DataFrame posicional) al esquema común."""
    frame = raw if isinstance(raw, pd.DataFrame) else pd.DataFrame(raw)
    if frame.empty:
        return empty_frame()
    frame = frame.iloc[:, :9].copy()
    frame.columns = RAW_FIELDS[:9]

    open_ms = to_milliseconds(pd.to_numeric(frame["open_time"]).to_numpy())
    index = pd.DatetimeIndex(pd.to_datetime(open_ms, unit="ms", utc=True), name=INDEX)
    data = {c: pd.to_numeric(frame[c]).astype("float64").to_numpy() for c in OHLCV}
    data["quote_volume"] = pd.to_numeric(frame["quote_volume"]).astype("float64").to_numpy()
    data["trades"] = pd.to_numeric(frame["trades"]).astype("int64").to_numpy()
    return finalize(pd.DataFrame(data, index=index))


def finalize(df: pd.DataFrame) -> pd.DataFrame:
    """Ordena, quita duplicados (gana la última versión) y fija los tipos."""
    if df.empty:
        return empty_frame()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df.index = pd.DatetimeIndex(df.index, name=INDEX).tz_convert("UTC").as_unit("ns")
    out = df[COLUMNS].copy()
    floats = [*OHLCV, "quote_volume"]
    out[floats] = out[floats].astype("float64")
    out["trades"] = out["trades"].astype("int64")
    return out
