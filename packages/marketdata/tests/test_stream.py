"""Pruebas del streaming contra un servidor websocket local (sin red externa)."""

import asyncio
import json

import pandas as pd
import pytest
from tm_marketdata.service import MarketDataService
from tm_marketdata.stream import StreamClosedError, parse_kline_message, stream_bars
from websockets.asyncio.server import serve

T0 = 1_767_225_600_000


def kline_event(open_ms: int, closed: bool, close: str = "87648.0") -> str:
    """Evento con el formato del stream `<symbol>@kline_<tf>` de Binance."""
    return json.dumps(
        {
            "e": "kline",
            "E": open_ms + 59_999,
            "s": "BTCUSDT",
            "k": {
                "t": open_ms,
                "T": open_ms + 59_999,
                "s": "BTCUSDT",
                "i": "1m",
                "o": "87648.21",
                "c": close,
                "h": "87648.22",
                "l": "87632.74",
                "v": "4.08049",
                "n": 1193,
                "x": closed,
                "q": "357625.776862",
            },
        }
    )


def test_parse_kline_message():
    assert parse_kline_message(kline_event(T0, closed=False)) is None
    assert parse_kline_message(json.dumps({"result": None, "id": 1})) is None
    bar = parse_kline_message(kline_event(T0, closed=True))
    assert bar.open_time == pd.Timestamp("2026-01-01", tz="UTC")
    assert bar.close == pytest.approx(87648.0)
    assert bar.trades == 1193
    # Microsegundos también se normalizan.
    assert parse_kline_message(kline_event(T0 * 1000, closed=True)).open_time == bar.open_time


async def _collect(n: int, **kwargs) -> list:
    bars = []
    async for bar in stream_bars("BTCUSDT", "1m", **kwargs):
        bars.append(bar)
        if len(bars) == n:
            break
    return bars


def test_stream_emits_only_closed_and_reconnects():
    connections = []
    paths = []

    async def handler(websocket):
        connections.append(websocket)
        paths.append(websocket.request.path)
        if len(connections) == 1:
            await websocket.send(kline_event(T0, closed=False))
            await websocket.send(kline_event(T0, closed=True))
            await websocket.send(kline_event(T0 + 60_000, closed=False))
            # Se corta la conexión a la mitad: el cliente debe reconectarse.
        else:
            # Tras reconectar, Binance puede repetir la última vela cerrada.
            await websocket.send(kline_event(T0, closed=True))
            await websocket.send(kline_event(T0 + 60_000, closed=True))
            await asyncio.sleep(1)

    async def scenario():
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            return await asyncio.wait_for(
                _collect(2, base_url=f"ws://127.0.0.1:{port}/ws", reconnect_delay=0.01), 5
            )

    bars = asyncio.run(scenario())
    assert [b.open_time.value // 1_000_000 for b in bars] == [T0, T0 + 60_000]
    assert len(connections) == 2
    assert paths[0] == "/ws/btcusdt@kline_1m"


def test_stream_gives_up_after_max_reconnects():
    async def scenario():
        # Puerto cerrado: cada intento falla con OSError.
        return await _collect(
            1, base_url="ws://127.0.0.1:9/ws", reconnect_delay=0.001, max_reconnects=2
        )

    with pytest.raises(StreamClosedError):
        asyncio.run(scenario())


def test_stream_rejects_invalid_timeframe():
    async def scenario():
        async for _ in stream_bars("BTCUSDT", "2h"):
            pass  # pragma: no cover

    with pytest.raises(ValueError):
        asyncio.run(scenario())


def test_service_stream_delegates(tmp_path):
    seen = {}

    class FakeConnection:
        def __init__(self, url):
            seen["url"] = url

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def __aiter__(self):
            async def messages():
                yield kline_event(T0, closed=True)

            return messages()

    async def scenario():
        svc = MarketDataService(data_dir=tmp_path, cache=None)
        async for bar in svc.stream_bars("eth/usdt", "1m", connect=FakeConnection):
            return bar

    bar = asyncio.run(scenario())
    assert bar.symbol == "BTCUSDT"
    assert seen["url"].endswith("/ethusdt@kline_1m")
