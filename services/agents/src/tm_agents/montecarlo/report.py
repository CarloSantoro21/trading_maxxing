"""Reporte de Monte Carlo y cálculo de métricas a partir de las secuencias simuladas."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray

Method = Literal["reshuffle", "bootstrap"]

# Percentiles que se reportan para cada distribución
SUMMARY_PERCENTILES = (5, 50, 95, 99)
# Percentiles del abanico de equity
FAN_PERCENTILES = (10, 50, 90)


@dataclass(frozen=True, slots=True)
class DistributionSummary:
    """Resumen de una distribución empírica (una muestra por simulación)."""

    mean: float
    std: float
    min: float
    max: float
    p5: float
    p50: float
    p95: float
    p99: float

    @classmethod
    def from_samples(cls, samples: NDArray[np.float64]) -> DistributionSummary:
        p5, p50, p95, p99 = np.percentile(samples, SUMMARY_PERCENTILES)
        return cls(
            mean=float(samples.mean()),
            std=float(samples.std()),
            min=float(samples.min()),
            max=float(samples.max()),
            p5=float(p5),
            p50=float(p50),
            p95=float(p95),
            p99=float(p99),
        )


@dataclass(frozen=True, slots=True, eq=False)
class EquityFan:
    """Percentiles del equity por paso, para graficar un abanico.

    Cada arreglo tiene longitud ``n_trades + 1``: el índice 0 es el capital
    inicial y el índice ``k`` es el equity después del trade ``k``.
    """

    p10: NDArray[np.float64]
    p50: NDArray[np.float64]
    p90: NDArray[np.float64]


@dataclass(frozen=True, slots=True, eq=False)
class MCReport:
    """Resultado de una corrida de Monte Carlo sobre la secuencia de trades.

    Los drawdowns se expresan como fracción positiva del pico (0.2 = caída de
    20 %) y los retornos como fracción del capital inicial.
    """

    method: Method
    n_simulations: int
    n_trades: int
    initial_capital: float
    ruin_level: float
    block_size: int
    max_drawdown: DistributionSummary
    final_return: DistributionSummary
    per_trade_return: DistributionSummary
    cagr: DistributionSummary | None
    probability_of_ruin: float
    equity_fan: EquityFan
    max_drawdowns: NDArray[np.float64]
    final_returns: NDArray[np.float64]


def build_report(
    paths: NDArray[np.float64],
    *,
    method: Method,
    initial_capital: float,
    ruin_level: float,
    block_size: int,
    trades_per_year: float | None,
) -> MCReport:
    """Calcula las métricas de un conjunto de secuencias de retornos ``(n, m)``.

    Modifica ``paths`` en su lugar para no duplicar memoria.
    """
    n_simulations, n_trades = paths.shape

    # Equity con el capital inicial como primer punto: el drawdown se mide
    # también contra el capital de arranque.
    equity = np.empty((n_simulations, n_trades + 1), dtype=np.float64)
    equity[:, 0] = initial_capital
    paths += 1.0
    np.cumprod(paths, axis=1, out=equity[:, 1:])
    equity[:, 1:] *= initial_capital

    peaks = np.maximum.accumulate(equity, axis=1)
    np.divide(equity, peaks, out=peaks)  # peaks ahora es equity / pico
    max_drawdowns = 1.0 - peaks.min(axis=1)

    growth = equity[:, -1] / initial_capital
    final_returns = growth - 1.0
    per_trade = growth ** (1.0 / n_trades) - 1.0
    cagr = None
    if trades_per_year is not None:
        cagr = DistributionSummary.from_samples((1.0 + per_trade) ** trades_per_year - 1.0)

    ruined = equity.min(axis=1) < ruin_level * initial_capital
    p10, p50, p90 = np.percentile(equity, FAN_PERCENTILES, axis=0)

    return MCReport(
        method=method,
        n_simulations=n_simulations,
        n_trades=n_trades,
        initial_capital=initial_capital,
        ruin_level=ruin_level,
        block_size=block_size,
        max_drawdown=DistributionSummary.from_samples(max_drawdowns),
        final_return=DistributionSummary.from_samples(final_returns),
        per_trade_return=DistributionSummary.from_samples(per_trade),
        cagr=cagr,
        probability_of_ruin=float(ruined.mean()),
        equity_fan=EquityFan(p10=p10, p50=p50, p90=p90),
        max_drawdowns=max_drawdowns,
        final_returns=final_returns,
    )
