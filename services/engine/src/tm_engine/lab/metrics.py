"""Métricas sobre la serie diaria de equity: retorno, Sharpe (365 días), drawdown y CAGR."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

DAYS_PER_YEAR = 365  # cripto opera todos los días


@dataclass(frozen=True)
class PerfStats:
    """Desempeño de un tramo; los retornos y el drawdown son fracciones (0.1 = 10 %)."""

    start: str
    end: str
    days: int
    years: float
    total_return: float
    cagr: float | None
    sharpe: float | None
    volatility: float | None
    max_drawdown: float
    final_equity: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def daily_returns(equity: pd.Series) -> pd.Series:
    """Retornos simples día a día (el primer punto de la serie es la base)."""
    return equity.pct_change().iloc[1:]


def sharpe_ratio(returns: pd.Series | np.ndarray) -> float | None:
    """Sharpe anualizado con 365 días y tasa libre de riesgo 0; `None` sin variación."""
    r = np.asarray(returns, dtype=float)
    if r.size < 2:
        return None
    std = r.std(ddof=1)
    if not np.isfinite(std) or std <= 1e-12:
        return None
    return float(r.mean() / std * math.sqrt(DAYS_PER_YEAR))


def max_drawdown(equity: pd.Series | np.ndarray) -> float:
    """Peor caída desde un máximo previo, como fracción positiva."""
    e = np.asarray(equity, dtype=float)
    if e.size == 0:
        return 0.0
    peaks = np.maximum.accumulate(e)
    return float(1.0 - (e / peaks).min())


def perf_stats(equity: pd.Series) -> PerfStats:
    """Estadísticas de una serie diaria de equity (el primer punto es el capital inicial)."""
    if len(equity) < 2:
        raise ValueError("la serie de equity necesita al menos dos puntos")
    rets = daily_returns(equity)
    days = len(rets)
    years = days / DAYS_PER_YEAR
    total = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    cagr = float((1.0 + total) ** (1.0 / years) - 1.0) if years > 0 and total > -1 else None
    sharpe = sharpe_ratio(rets)
    vol = float(rets.std(ddof=1) * math.sqrt(DAYS_PER_YEAR)) if days > 1 else None
    return PerfStats(
        start=pd.Timestamp(equity.index[0]).date().isoformat(),
        end=pd.Timestamp(equity.index[-1]).date().isoformat(),
        days=days,
        years=years,
        total_return=total,
        cagr=cagr,
        sharpe=sharpe,
        volatility=vol,
        max_drawdown=max_drawdown(equity),
        final_equity=float(equity.iloc[-1]),
    )


def window_slice(equity: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    """Puntos diarios de `[start, end]` (incluye el de `start` como base del tramo)."""
    return equity[(equity.index >= start) & (equity.index <= end)]
