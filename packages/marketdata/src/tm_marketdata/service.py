"""MarketDataService: velas históricas (disco primero, luego REST) y streaming."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pandas as pd

from tm_marketdata.cache import BarCache, bars_key, cache_from_env
from tm_marketdata.rest import BinanceRestClient
from tm_marketdata.schema import COLUMNS, OHLCV, empty_frame, finalize, normalize_klines
from tm_marketdata.storage import ParquetStore
from tm_marketdata.stream import Bar, stream_bars
from tm_marketdata.timeframes import CACHE_TTL, TimeLike, normalize_symbol, tf_ms, to_ms

logger = logging.getLogger(__name__)


def _now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def missing_segments(
    local: pd.DataFrame, start_ms: int, end_ms: int, step: int
) -> list[tuple[int, int]]:
    """Tramos de `[start_ms, end_ms)` que el disco no cubre: antes y después de lo local.

    Los huecos internos no se piden: son huecos reales de Binance (mantenimientos)
    que también están en los archivos de data.binance.vision.
    """
    if local.empty:
        return [(start_ms, end_ms)]
    segments = []
    first = local.index[0].value // 1_000_000
    last = local.index[-1].value // 1_000_000
    aligned_start = -(-start_ms // step) * step  # primera apertura >= start
    if first > aligned_start:
        segments.append((start_ms, first))
    if last + step < end_ms:
        segments.append((last + step, end_ms))
    return segments


class MarketDataService:
    """Punto único de acceso a velas para agents, engine y api."""

    def __init__(
        self,
        store: ParquetStore | None = None,
        rest: BinanceRestClient | None = None,
        cache: BarCache | None = None,
        *,
        data_dir: str | Path | None = None,
        clock: Callable[[], pd.Timestamp] = _now,
    ) -> None:
        self.store = store or ParquetStore(data_dir)
        self.rest = rest or BinanceRestClient()
        self.cache = cache if cache is not None else cache_from_env()
        self._clock = clock

    def get_bars(
        self,
        symbol: str,
        tf: str,
        start: TimeLike,
        end: TimeLike | None = None,
        *,
        include_extra: bool = False,
    ) -> pd.DataFrame:
        """Velas **cerradas** con `start <= open_time < end`, indexadas por `open_time` (UTC).

        Columnas `open, high, low, close, volume` (float). Con `include_extra=True` agrega
        `quote_volume` y `trades`. `end=None` significa "hasta la última vela cerrada".
        """
        symbol = normalize_symbol(symbol)
        step = tf_ms(tf)
        columns = COLUMNS if include_extra else OHLCV

        now_ms = to_ms(self._clock())
        # Una vela está cerrada si open_time + step <= ahora; eso evita el look-ahead.
        closed_end = (now_ms // step) * step
        start_ms = to_ms(start)
        end_ms = closed_end if end is None else min(to_ms(end), closed_end)
        if end_ms <= start_ms:
            return empty_frame()[columns]

        start_ts = pd.Timestamp(start_ms, unit="ms", tz="UTC")
        end_ts = pd.Timestamp(end_ms, unit="ms", tz="UTC")
        local = self.store.read(symbol, tf, start_ts, end_ts)
        segments = missing_segments(local, start_ms, end_ms, step)
        if not segments:
            return local[columns]

        key = bars_key(symbol, tf, start_ms, end_ms, include_extra)
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        frames = [local]
        for seg_start, seg_end in segments:
            logger.debug("REST %s %s [%s, %s)", symbol, tf, seg_start, seg_end)
            frames.append(normalize_klines(self.rest.klines(symbol, tf, seg_start, seg_end)))
        frames = [f for f in frames if not f.empty]
        df = finalize(pd.concat(frames)) if frames else empty_frame()
        df = df[(df.index >= start_ts) & (df.index < end_ts)][columns]
        self.cache.set(key, df, CACHE_TTL[tf])
        return df

    def stream_bars(self, symbol: str, tf: str, **kwargs) -> AsyncIterator[Bar]:
        """Velas cerradas en tiempo real; ver `tm_marketdata.stream.stream_bars`."""
        return stream_bars(normalize_symbol(symbol), tf, **kwargs)
