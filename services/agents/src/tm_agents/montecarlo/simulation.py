"""Punto de entrada del Monte Carlo: valida entradas, remuestrea y arma el reporte."""

from __future__ import annotations

import math
import numbers
from typing import get_args

import numpy as np
from numpy.typing import ArrayLike, NDArray

from tm_agents.montecarlo.report import MCReport, Method, build_report
from tm_agents.montecarlo.resampling import bootstrap_paths, reshuffle_paths

Seed = int | np.random.Generator


def simulate(
    trades: ArrayLike,
    n_simulations: int,
    *,
    seed: Seed,
    method: Method = "reshuffle",
    initial_capital: float = 1.0,
    ruin_level: float = 0.5,
    block_size: int = 1,
    trades_per_year: float | None = None,
    horizon: int | None = None,
) -> MCReport:
    """Corre ``n_simulations`` remuestreos de ``trades`` y devuelve un :class:`MCReport`.

    Args:
        trades: retornos fraccionales por trade (``0.012`` = +1.2 %). Deben ser
            finitos y mayores o iguales a ``-1``.
        n_simulations: número de secuencias simuladas.
        seed: semilla entera o ``numpy.random.Generator``. Es obligatoria para
            que cada corrida sea reproducible.
        method: ``"reshuffle"`` (permutación sin reemplazo) o ``"bootstrap"``
            (con reemplazo).
        initial_capital: capital al inicio de cada secuencia.
        ruin_level: fracción del capital inicial que define la ruina. Una
            simulación se considera arruinada si su equity cae por debajo de
            ``ruin_level * initial_capital`` en cualquier punto del camino.
        block_size: tamaño de bloque del bootstrap (1 = bootstrap iid). Solo
            aplica con ``method="bootstrap"``.
        trades_per_year: si se indica, se reporta también la distribución del
            CAGR anualizando el retorno geométrico por trade.
        horizon: número de pasos de cada secuencia (por defecto, los de ``trades``).
            Solo aplica con ``method="bootstrap"``: con 365 retornos diarios mide el
            drawdown "de un año", que es lo que usa el criterio de aprobación.
    """
    returns = _validate_trades(trades)
    n_trades = returns.shape[0]
    _validate_positive_int("n_simulations", n_simulations)
    _validate_positive_int("block_size", block_size)
    if method not in get_args(Method):
        raise ValueError(f"method debe ser 'reshuffle' o 'bootstrap', no {method!r}")
    if method == "reshuffle" and block_size != 1:
        raise ValueError("block_size solo aplica con method='bootstrap'")
    if horizon is not None:
        _validate_positive_int("horizon", horizon)
        if method == "reshuffle" and horizon != n_trades:
            raise ValueError("horizon solo aplica con method='bootstrap'")
    if block_size > n_trades:
        raise ValueError(f"block_size ({block_size}) no puede exceder el número de trades")
    if not _is_real(initial_capital) or not math.isfinite(initial_capital) or initial_capital <= 0:
        raise ValueError("initial_capital debe ser un número finito mayor a 0")
    if not _is_real(ruin_level) or not 0.0 <= ruin_level < 1.0:
        raise ValueError("ruin_level debe estar en el intervalo [0, 1)")
    if trades_per_year is not None and (
        not _is_real(trades_per_year) or not math.isfinite(trades_per_year) or trades_per_year <= 0
    ):
        raise ValueError("trades_per_year debe ser un número finito mayor a 0")
    rng = _make_rng(seed)

    if method == "reshuffle":
        paths = reshuffle_paths(returns, n_simulations, rng)
    else:
        paths = bootstrap_paths(returns, n_simulations, rng, block_size, length=horizon)

    return build_report(
        paths,
        method=method,
        initial_capital=float(initial_capital),
        ruin_level=float(ruin_level),
        block_size=block_size,
        trades_per_year=None if trades_per_year is None else float(trades_per_year),
    )


def reshuffle(
    trades: ArrayLike,
    n_simulations: int,
    *,
    seed: Seed,
    initial_capital: float = 1.0,
    ruin_level: float = 0.5,
    trades_per_year: float | None = None,
) -> MCReport:
    """Monte Carlo por permutación de trades (sin reemplazo). Ver :func:`simulate`."""
    return simulate(
        trades,
        n_simulations,
        seed=seed,
        method="reshuffle",
        initial_capital=initial_capital,
        ruin_level=ruin_level,
        trades_per_year=trades_per_year,
    )


def bootstrap(
    trades: ArrayLike,
    n_simulations: int,
    *,
    seed: Seed,
    block_size: int = 1,
    initial_capital: float = 1.0,
    ruin_level: float = 0.5,
    trades_per_year: float | None = None,
    horizon: int | None = None,
) -> MCReport:
    """Monte Carlo por bootstrap (con reemplazo, opcional por bloques). Ver :func:`simulate`."""
    return simulate(
        trades,
        n_simulations,
        seed=seed,
        method="bootstrap",
        block_size=block_size,
        initial_capital=initial_capital,
        ruin_level=ruin_level,
        trades_per_year=trades_per_year,
        horizon=horizon,
    )


def _validate_trades(trades: ArrayLike) -> NDArray[np.float64]:
    if isinstance(trades, str | bytes):
        raise TypeError("trades debe ser un arreglo de retornos, no un texto")
    try:
        returns = np.asarray(trades, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise TypeError("trades debe ser un arreglo numérico de retornos") from exc
    if returns.ndim != 1:
        raise ValueError(f"trades debe ser unidimensional, tiene {returns.ndim} dimensiones")
    if returns.size == 0:
        raise ValueError("trades está vacío: se necesita al menos un trade")
    if not np.all(np.isfinite(returns)):
        raise ValueError("trades contiene valores NaN o infinitos")
    if np.any(returns < -1.0):
        raise ValueError("trades contiene retornos menores a -1 (pérdida mayor al 100 %)")
    return returns


def _validate_positive_int(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, numbers.Integral):
        raise TypeError(f"{name} debe ser un entero, no {type(value).__name__}")
    if value < 1:
        raise ValueError(f"{name} debe ser mayor o igual a 1")


def _is_real(value: object) -> bool:
    return isinstance(value, numbers.Real) and not isinstance(value, bool)


def _make_rng(seed: Seed) -> np.random.Generator:
    if isinstance(seed, np.random.Generator):
        return seed
    if isinstance(seed, bool) or not isinstance(seed, numbers.Integral):
        raise TypeError("seed debe ser un entero o un numpy.random.Generator (es obligatoria)")
    return np.random.default_rng(int(seed))


__all__ = ["bootstrap", "reshuffle", "simulate"]
