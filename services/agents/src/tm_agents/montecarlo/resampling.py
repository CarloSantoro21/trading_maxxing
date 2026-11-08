"""Métodos de remuestreo de la secuencia de trades.

Ambos métodos reciben un vector de retornos de longitud ``m`` y devuelven una
matriz ``(n_simulations, m)`` donde cada fila es una secuencia simulada. Todo
se hace con operaciones vectorizadas de numpy, sin loops de Python sobre las
simulaciones.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


def reshuffle_paths(
    returns: NDArray[np.float64], n_simulations: int, rng: np.random.Generator
) -> NDArray[np.float64]:
    """Permuta los trades sin reemplazo (cada fila es una permutación independiente).

    Conserva el multiset de retornos, por lo que el retorno final multiplicativo
    es el mismo en todas las filas; solo cambia el camino (y con él el drawdown).
    """
    tiled = np.tile(returns, (n_simulations, 1))
    return rng.permuted(tiled, axis=1)


def bootstrap_paths(
    returns: NDArray[np.float64],
    n_simulations: int,
    rng: np.random.Generator,
    block_size: int = 1,
    length: int | None = None,
) -> NDArray[np.float64]:
    """Remuestrea los trades con reemplazo.

    Con ``block_size == 1`` es el bootstrap iid clásico. Con ``block_size > 1``
    es un block bootstrap circular: se eligen inicios al azar y se copian bloques
    contiguos de ``block_size`` trades (dando la vuelta al final del arreglo),
    lo que conserva la autocorrelación de corto plazo. Al ser circular, cada
    posición tiene la misma probabilidad de aparecer y la media es insesgada.

    ``length`` es el número de pasos de cada secuencia (por defecto, los mismos que
    ``returns``). Sirve para medir el drawdown en un horizonte fijo, por ejemplo 365
    retornos diarios (un año), sin importar cuántos días tenga el backtest.
    """
    n_trades = returns.shape[0]
    steps = n_trades if length is None else length
    if block_size == 1:
        idx = rng.integers(0, n_trades, size=(n_simulations, steps))
        return returns[idx]

    n_blocks = -(-steps // block_size)  # techo de la división
    starts = rng.integers(0, n_trades, size=(n_simulations, n_blocks, 1))
    offsets = np.arange(block_size).reshape(1, 1, block_size)
    idx = (starts + offsets) % n_trades
    idx = idx.reshape(n_simulations, n_blocks * block_size)[:, :steps]
    return returns[idx]
