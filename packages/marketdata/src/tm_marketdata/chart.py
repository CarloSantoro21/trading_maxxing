"""Render determinista de gráficos OHLCV con EMAs, volumen y conos de proyección.

Este módulo expone `render_chart_png(spec)`, la base visual del
`StrategyAuditorAgent` (#108). Dado un `ChartSpec` genera un PNG con:
    - velas OHLC (mplfinance)
    - EMAs superpuestas (calculadas inline a partir del cierre)
    - volumen en un subplot inferior
    - conos opcionales P10/P50/P90 estilo Kronos a futuro

El objetivo es que dos llamadas con el mismo input produzcan exactamente los
mismos bytes PNG (sha256 idéntico), para que el agente pueda cachear y
comparar auditorías sin falsos cambios visuales.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import matplotlib

# Backend sin pantalla antes de importar pyplot; obligatorio para headless/CI.
matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import mplfinance as mpf  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

# Ajustes globales para que el render sea determinista:
#   - `svg.hashsalt` fija cualquier hash interno de matplotlib.
#   - La familia de fuentes se fija a DejaVu Sans (siempre presente en matplotlib).
matplotlib.rcParams["svg.hashsalt"] = "tm"
matplotlib.rcParams["font.family"] = "DejaVu Sans"


@dataclass(frozen=True)
class ChartCones:
    """Conos de proyección (estilo Kronos): percentiles P10/P50/P90 a futuro."""

    future_index: pd.DatetimeIndex
    p10: np.ndarray
    p50: np.ndarray
    p90: np.ndarray


@dataclass(frozen=True)
class ChartSpec:
    """Entrada del render: velas, EMAs, conos opcionales y dimensiones."""

    ohlcv: pd.DataFrame
    ema_windows: tuple[int, ...] = (50, 200)
    cones: ChartCones | None = None
    title: str = ""
    width_px: int = 1280
    height_px: int = 720
    dpi: int = 96


def _prepare_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Devuelve un DataFrame apto para mplfinance (OHLCV con índice temporal).

    mplfinance espera columnas capitalizadas: Open/High/Low/Close/Volume.
    """
    required = ("open", "high", "low", "close", "volume")
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Faltan columnas OHLCV: {missing}")
    out = df.loc[:, list(required)].copy()
    out.columns = ["Open", "High", "Low", "Close", "Volume"]
    # mplfinance requiere un índice tipo datetime; si no lo es, intentamos convertir.
    if not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index, utc=True)
    return out


def _ema_series(close: pd.Series, span: int) -> pd.Series:
    """EMA inline: `pandas.Series.ewm(span=N, adjust=False).mean()`."""
    return close.ewm(span=span, adjust=False).mean()


def _build_addplots(
    prepared: pd.DataFrame,
    ema_windows: tuple[int, ...],
    cones: ChartCones | None,
) -> list:
    """Construye los addplots: una línea por EMA y, si aplica, los tres conos."""
    addplots: list = []
    # Paleta fija para que los colores sean deterministas (no dependen del ciclo global).
    ema_colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd"]
    for i, window in enumerate(ema_windows):
        if window <= 0:
            continue
        ema = _ema_series(prepared["Close"], window)
        # Si todo es NaN (historial muy corto) mplfinance se queja, lo saltamos.
        if ema.notna().any():
            addplots.append(
                mpf.make_addplot(
                    ema,
                    color=ema_colors[i % len(ema_colors)],
                    width=1.0,
                )
            )

    if cones is not None and len(cones.future_index) > 0:
        # Alineamos los conos al eje del chart: se añade al final de la serie histórica
        # una ventana con NaN en OHLC y los valores del cono en su propia columna.
        total_index = prepared.index.append(cones.future_index)
        for values, color in (
            (cones.p10, "#888888"),
            (cones.p50, "#444444"),
            (cones.p90, "#888888"),
        ):
            series = pd.Series(np.nan, index=total_index, dtype="float64")
            series.iloc[-len(cones.future_index) :] = values
            addplots.append(mpf.make_addplot(series, color=color, width=1.0))

    return addplots


def render_chart_png(spec: ChartSpec) -> bytes:
    """Renderiza el `ChartSpec` a PNG y devuelve los bytes.

    Determinismo:
        - Backend Agg + `svg.hashsalt` fijo + fuente fija.
        - Metadatos PNG vaciados (`Software`, `Creation Time`) para que el header
          no incluya la fecha de generación.
        - Se cierra la figura al terminar para evitar fugas.

    Datos insuficientes: si el DataFrame está vacío o es muy corto para las EMAs,
    no se lanza excepción; se dibuja lo que se pueda (incluso un gráfico vacío).
    """
    prepared = _prepare_ohlcv(spec.ohlcv)

    figsize = (spec.width_px / spec.dpi, spec.height_px / spec.dpi)

    # Estilo neutro y reproducible.
    style = mpf.make_mpf_style(
        base_mpf_style="classic",
        rc={"font.family": "DejaVu Sans"},
    )

    addplots = _build_addplots(prepared, spec.ema_windows, spec.cones)

    # Si no hay velas (DataFrame vacío) devolvemos una figura mínima: mplfinance
    # fallaría, así que generamos un PNG casi en blanco con matplotlib directo.
    if prepared.empty:
        fig, ax = plt.subplots(figsize=figsize, dpi=spec.dpi)
        ax.set_title(spec.title)
        ax.set_axis_off()
    else:
        try:
            fig, _axes = mpf.plot(
                prepared,
                type="candle",
                style=style,
                addplot=addplots if addplots else None,
                volume=True,
                figsize=figsize,
                figscale=1.0,
                returnfig=True,
                title=spec.title,
                warn_too_much_data=10_000_000,
                tight_layout=True,
            )
        except (ValueError, IndexError):
            # Historial demasiado corto para que mplfinance dibuje; degradamos a
            # un plot de cierre sin romper (la consigna pide no lanzar).
            fig, ax = plt.subplots(figsize=figsize, dpi=spec.dpi)
            if "Close" in prepared.columns and not prepared["Close"].empty:
                ax.plot(prepared.index, prepared["Close"].to_numpy(), color="#1f77b4")
            ax.set_title(spec.title)

    buf = io.BytesIO()
    fig.savefig(
        buf,
        format="png",
        dpi=spec.dpi,
        metadata={"Software": "", "Creation Time": ""},
    )
    plt.close(fig)
    return buf.getvalue()


__all__ = ["ChartCones", "ChartSpec", "render_chart_png"]
