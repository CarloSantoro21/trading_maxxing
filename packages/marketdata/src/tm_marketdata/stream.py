"""Streaming de velas cerradas sobre el websocket público de Binance."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

import pandas as pd
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import WebSocketException

from tm_marketdata.schema import to_milliseconds
from tm_marketdata.timeframes import tf_ms

logger = logging.getLogger(__name__)

STREAM_URL = "wss://stream.binance.com:9443/ws"


class StreamClosedError(ConnectionError):
    """Se agotaron los intentos de reconexión."""


@dataclass(frozen=True)
class Bar:
    """Una vela cerrada recibida por el websocket."""

    symbol: str
    tf: str
    open_time: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
    trades: int


def parse_kline_message(raw: str | bytes, tf: str | None = None) -> Bar | None:
    """Convierte un evento `kline` en `Bar`; devuelve None si la vela sigue abierta."""
    message = json.loads(raw)
    kline = message.get("k") if isinstance(message, dict) else None
    if not kline or not kline.get("x"):
        return None
    open_ms = int(to_milliseconds([int(kline["t"])])[0])
    return Bar(
        symbol=str(kline["s"]),
        tf=str(kline.get("i", tf)),
        open_time=pd.Timestamp(open_ms, unit="ms", tz="UTC"),
        open=float(kline["o"]),
        high=float(kline["h"]),
        low=float(kline["l"]),
        close=float(kline["c"]),
        volume=float(kline["v"]),
        quote_volume=float(kline["q"]),
        trades=int(kline["n"]),
    )


async def stream_bars(
    symbol: str,
    tf: str,
    *,
    base_url: str = STREAM_URL,
    connect: Callable = ws_connect,
    reconnect_delay: float = 1.0,
    max_delay: float = 30.0,
    max_reconnects: int | None = None,
) -> AsyncIterator[Bar]:
    """Emite solo velas cerradas (`k.x == true`) y se reconecta solo con backoff.

    Binance corta cada conexión a las 24 h; tras reconectar se descartan las velas
    repetidas para que el consumidor no procese dos veces la misma.
    """
    tf_ms(tf)  # valida el timeframe
    url = f"{base_url.rstrip('/')}/{symbol.lower()}@kline_{tf}"
    failures = 0
    last_open: pd.Timestamp | None = None
    while True:
        try:
            async with connect(url) as websocket:
                logger.info("conectado a %s", url)
                async for raw in websocket:
                    bar = parse_kline_message(raw, tf)
                    if bar is None or (last_open is not None and bar.open_time <= last_open):
                        continue
                    failures = 0
                    last_open = bar.open_time
                    yield bar
            logger.warning("el servidor cerró %s", url)
        except (OSError, TimeoutError, WebSocketException) as exc:
            logger.warning("conexión perdida con %s: %s", url, exc)
        failures += 1
        if max_reconnects is not None and failures > max_reconnects:
            raise StreamClosedError(f"sin conexión a {url} tras {max_reconnects} reintentos")
        await asyncio.sleep(min(max_delay, reconnect_delay * 2 ** (failures - 1)))
