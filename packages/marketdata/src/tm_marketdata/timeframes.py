"""Timeframes soportados y utilidades de tiempo (todo en UTC y milisegundos)."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd

# Duración de cada vela en milisegundos. Los nombres coinciden con los intervalos de Binance.
TIMEFRAMES: dict[str, int] = {
    "1m": 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1d": 24 * 60 * 60_000,
}

# TTL del cache de Redis por timeframe, en segundos. Una respuesta reciente solo cambia
# cuando cierra la siguiente vela, así que el TTL nunca supera la duración de la vela.
CACHE_TTL: dict[str, int] = {
    "1m": 30,
    "15m": 300,
    "30m": 600,
    "1d": 3_600,
}

TimeLike = str | date | datetime | pd.Timestamp | int


def tf_ms(tf: str) -> int:
    """Duración de la vela en milisegundos; falla si el timeframe no está soportado."""
    try:
        return TIMEFRAMES[tf]
    except KeyError:
        valid = ", ".join(TIMEFRAMES)
        raise ValueError(f"timeframe no soportado: {tf!r} (válidos: {valid})") from None


def to_utc(value: TimeLike) -> pd.Timestamp:
    """Convierte a un Timestamp con zona UTC. Las fechas sin zona se interpretan como UTC.

    Un entero se interpreta como milisegundos desde epoch.
    """
    ts = pd.Timestamp(value, unit="ms") if isinstance(value, int) else pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def to_ms(value: TimeLike) -> int:
    """Milisegundos desde epoch de un instante."""
    return to_utc(value).value // 1_000_000


def normalize_symbol(symbol: str) -> str:
    """`BTC/USDT`, `btc-usdt` o `btcusdt` -> `BTCUSDT`."""
    return symbol.replace("/", "").replace("-", "").replace("_", "").upper()
