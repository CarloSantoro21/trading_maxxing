"""Configuración del walk-forward, ventanas in-sample/out-of-sample y guarda del holdout."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal

import pandas as pd

Engine = Literal["nautilus", "vector"]

DATA_START = "2020-01-01"
OOS_START = "2021-01-01"
OOS_END = "2025-01-01"  # exclusivo: el último tramo es 2024-T4
HOLDOUT_START = "2025-01-01"
HOLDOUT_END = "2026-01-01"
# Techo global del drawdown p95 a tamaño completo (el del nivel de riesgo 5); cada usuario
# invierte después la fracción min(1, drawdown de su nivel / p95). Ver criterios-de-aprobacion.md.
GLOBAL_MAX_DRAWDOWN = 0.40
# Drawdown máximo tolerado por nivel de riesgo (propuesta, sección 3.4)
RISK_LEVEL_DRAWDOWN = {1: 0.10, 2: 0.15, 3: 0.20, 4: 0.30, 5: 0.40}


class HoldoutError(RuntimeError):
    """Se pidió tocar el holdout (2025) sin la bandera explícita `include_holdout`."""


def utc(value: str | pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


@dataclass(frozen=True)
class Window:
    """Un tramo del walk-forward: se ajusta en `[is_start, is_end)` y se prueba en el OOS."""

    index: int
    is_start: pd.Timestamp
    is_end: pd.Timestamp
    oos_start: pd.Timestamp
    oos_end: pd.Timestamp

    @property
    def label(self) -> str:
        """`2021-T1`: año y trimestre (o mes de inicio si el tramo no es trimestral)."""
        start = self.oos_start
        if start.month % 3 == 1 and self.oos_end == start + pd.DateOffset(months=3):
            return f"{start.year}-T{(start.month - 1) // 3 + 1}"
        return f"{start:%Y-%m}"

    def to_dict(self) -> dict[str, str]:
        return {
            "label": self.label,
            "is_start": self.is_start.date().isoformat(),
            "is_end": self.is_end.date().isoformat(),
            "oos_start": self.oos_start.date().isoformat(),
            "oos_end": self.oos_end.date().isoformat(),
        }


@dataclass(frozen=True)
class WalkForwardConfig:
    """Parámetros de una corrida del laboratorio.

    - `is_months` / `oos_months`: tamaño de la ventana de ajuste y de prueba; el walk-forward
      avanza `step_months` (por defecto, lo mismo que el OOS).
    - `oos_start` / `oos_end`: primer y último instante (exclusivo) de prueba.
    - `holdout_start`: desde ahí los datos son el examen final. Sin `include_holdout=True` no
      se cargan velas a partir de esa fecha y pedir ventanas que la crucen lanza `HoldoutError`.
    - `grid`: lista de combinaciones de parámetros; `None` usa la cuadrícula de la estrategia.
    - `engine`: `nautilus` ejecuta el OOS con `run_backtest` (y lo compara con el simulador
      vectorizado); `vector` solo usa el simulador (más rápido, para pruebas).
    """

    strategy: str
    symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")
    data_start: str = DATA_START
    oos_start: str = OOS_START
    oos_end: str = OOS_END
    is_months: int = 12
    oos_months: int = 3
    step_months: int | None = None
    holdout_start: str = HOLDOUT_START
    include_holdout: bool = False
    capital: float = 100_000.0
    fee: float = 0.001
    grid: Sequence[Mapping[str, Any]] | None = None
    engine: Engine = "nautilus"
    n_simulations: int = 10_000
    block_size: int = 20
    seed: int = 42
    max_drawdown: float = GLOBAL_MAX_DRAWDOWN
    data_dir: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if min(self.is_months, self.oos_months, self.step_months or 1) < 1:
            raise ValueError("los tamaños de ventana deben ser de al menos 1 mes")
        if not self.symbols:
            raise ValueError("hace falta al menos un símbolo")
        if self.capital <= 0 or not 0 <= self.fee < 0.1:
            raise ValueError("capital debe ser positivo y fee estar en [0, 0.1)")
        if self.engine not in ("nautilus", "vector"):
            raise ValueError("engine debe ser 'nautilus' o 'vector'")
        if utc(self.oos_end) <= utc(self.oos_start):
            raise ValueError("oos_end debe ser posterior a oos_start")

    @property
    def data_end(self) -> pd.Timestamp:
        """Último instante (exclusivo) de datos que se puede leer."""
        end = utc(self.oos_end)
        return end if self.include_holdout else min(end, utc(self.holdout_start))

    def holdout(self, end: str = HOLDOUT_END) -> WalkForwardConfig:
        """La misma configuración, pero probando sobre el holdout (con la bandera explícita)."""
        return replace(self, oos_start=self.holdout_start, oos_end=end, include_holdout=True)

    def windows(self) -> list[Window]:
        """Tramos del walk-forward; con los valores por defecto son 16 (2021-T1 a 2024-T4)."""
        start, end = utc(self.oos_start), utc(self.oos_end)
        if not self.include_holdout and end > utc(self.holdout_start):
            raise HoldoutError(
                f"las ventanas llegan a {end.date()} y el holdout empieza el "
                f"{utc(self.holdout_start).date()}: usa include_holdout=True (--holdout)"
            )
        step = pd.DateOffset(months=self.step_months or self.oos_months)
        oos = pd.DateOffset(months=self.oos_months)
        is_len = pd.DateOffset(months=self.is_months)
        out: list[Window] = []
        cursor = start
        while cursor < end:
            oos_end = min(cursor + oos, end)
            is_start = max(cursor - is_len, utc(self.data_start))
            out.append(Window(len(out), is_start, cursor, cursor, oos_end))
            cursor = cursor + step
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "symbols": list(self.symbols),
            "data_start": self.data_start,
            "oos_start": self.oos_start,
            "oos_end": self.oos_end,
            "is_months": self.is_months,
            "oos_months": self.oos_months,
            "step_months": self.step_months or self.oos_months,
            "holdout_start": self.holdout_start,
            "include_holdout": self.include_holdout,
            "capital": self.capital,
            "fee": self.fee,
            "engine": self.engine,
            "n_simulations": self.n_simulations,
            "block_size": self.block_size,
            "seed": self.seed,
            "max_drawdown": self.max_drawdown,
        }
