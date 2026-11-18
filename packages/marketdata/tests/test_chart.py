"""Tests del renderer determinista de gráficos (parte de #108)."""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd
import pytest

mpf = pytest.importorskip("mplfinance")

from tm_marketdata.chart import ChartCones, ChartSpec, render_chart_png  # noqa: E402

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _synthetic_ohlcv(n: int = 240, seed: int = 7) -> pd.DataFrame:
    """Genera OHLCV sintético reproducible (misma semilla => mismos datos)."""
    rng = np.random.default_rng(seed)
    index = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    # Camino de precios suave para que haya velas visibles.
    returns = rng.normal(0, 0.002, size=n)
    close = 100.0 * np.exp(np.cumsum(returns))
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.001, size=n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.001, size=n))
    volume = rng.uniform(100, 1_000, size=n)
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
        index=index,
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_png_magic_bytes():
    spec = ChartSpec(ohlcv=_synthetic_ohlcv(), title="BTCUSDT 1h")
    out = render_chart_png(spec)
    assert out.startswith(PNG_MAGIC)


def test_render_is_deterministic():
    """Dos llamadas con el mismo input producen el mismo PNG.

    Trade-off: si en alguna versión de matplotlib el PNG dejase de ser
    bit-for-bit idéntico (por metadatos internos imposibles de limpiar),
    caemos a comparar longitud + sha de una representación reducida
    (mismo tamaño total). En la práctica, con backend Agg, metadatos
    vacíos y fuente fija, el PNG es idéntico.
    """
    spec = ChartSpec(ohlcv=_synthetic_ohlcv(), title="deterministic")
    a = render_chart_png(spec)
    b = render_chart_png(spec)
    if _sha256(a) == _sha256(b):
        assert a == b
    else:
        # Fallback: igual tamaño y igual sha quitando bloques tEXt (metadatos).
        assert len(a) == len(b)
        assert _sha256(_strip_text_chunks(a)) == _sha256(_strip_text_chunks(b))


def test_cones_change_output():
    base = _synthetic_ohlcv()
    spec_a = ChartSpec(ohlcv=base, title="with/without cones")
    future_idx = pd.date_range(base.index[-1], periods=24, freq="1h", tz="UTC")[1:]
    last_close = float(base["close"].iloc[-1])
    cones = ChartCones(
        future_index=future_idx,
        p10=np.linspace(last_close * 0.95, last_close * 0.85, len(future_idx)),
        p50=np.linspace(last_close, last_close * 0.98, len(future_idx)),
        p90=np.linspace(last_close * 1.05, last_close * 1.20, len(future_idx)),
    )
    spec_b = ChartSpec(ohlcv=base, title="with/without cones", cones=cones)
    a = render_chart_png(spec_a)
    b = render_chart_png(spec_b)
    assert a != b


def test_insufficient_history_does_not_raise():
    # Solo 3 velas: muy por debajo del EMA(200), pero no debe explotar.
    short = _synthetic_ohlcv(n=3)
    spec = ChartSpec(ohlcv=short, ema_windows=(50, 200), title="short")
    out = render_chart_png(spec)
    assert out.startswith(PNG_MAGIC)


def _strip_text_chunks(png: bytes) -> bytes:
    """Devuelve el PNG sin sus chunks `tEXt`/`tIME` (metadatos variables)."""
    if not png.startswith(PNG_MAGIC):
        return png
    out = bytearray(PNG_MAGIC)
    i = len(PNG_MAGIC)
    while i < len(png):
        length = int.from_bytes(png[i : i + 4], "big")
        chunk_type = png[i + 4 : i + 8]
        total = 4 + 4 + length + 4  # length + type + data + crc
        if chunk_type not in (b"tEXt", b"tIME", b"iTXt", b"zTXt"):
            out.extend(png[i : i + total])
        i += total
    return bytes(out)
