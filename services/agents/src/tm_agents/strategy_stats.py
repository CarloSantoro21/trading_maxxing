"""Estado del ciclo de vida y estadísticas validadas de una estrategia.

Es la vista que necesita el `PortfolioAllocator` de cada estrategia: en qué estado del ciclo
de vida está (propuesta, sección 3.3) y qué midió el laboratorio (volatilidad, Sharpe
out-of-sample y, opcionalmente, sus retornos y el drawdown p95 del Monte Carlo). Todavía no
depende de la tabla `strategies` de Supabase; el repositorio que la lea solo tendrá que
construir estos objetos.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from tm_shared.contracts import Horizon


class StrategyState(StrEnum):
    """Estados del ciclo de vida: research → lab → shadow → active → probation → retired."""

    RESEARCH = "research"
    LAB = "lab"
    SHADOW = "shadow"
    ACTIVE = "active"
    PROBATION = "probation"
    RETIRED = "retired"

    @property
    def gets_capital(self) -> bool:
        """True si en este estado la estrategia opera con capital (activa u observación)."""
        return self in (StrategyState.ACTIVE, StrategyState.PROBATION)


@dataclass(frozen=True)
class StrategyStats:
    """Estadísticas validadas de una estrategia.

    Attributes:
        name: nombre con el que la estrategia está registrada (`register_strategy`).
        state: estado actual del ciclo de vida.
        volatility: volatilidad validada de sus retornos (fracción, por ejemplo la desviación
            estándar anualizada del walk-forward). Debe ser finita y no negativa.
        oos_sharpe: Sharpe out-of-sample del laboratorio.
        horizon: horizonte en el que opera la estrategia, para los pesos por horizonte.
            ``None`` = neutral (peso 1 con cualquier perfil).
        returns: retornos periódicos opcionales, alineados en el tiempo entre estrategias,
            para estimar correlaciones cuando no se pasa una matriz.
        max_drawdown_p95: drawdown máximo p95 de su Monte Carlo (fracción), si se conoce.
    """

    name: str
    state: StrategyState
    volatility: float
    oos_sharpe: float
    horizon: Horizon | None = None
    returns: tuple[float, ...] | None = field(default=None, repr=False)
    max_drawdown_p95: float | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name no puede estar vacío")
        object.__setattr__(self, "state", StrategyState(self.state))
        if math.isnan(self.volatility) or self.volatility < 0:
            raise ValueError("volatility debe ser mayor o igual a 0")
        if self.returns is not None:
            object.__setattr__(self, "returns", _as_returns(self.returns))
        if self.max_drawdown_p95 is not None and not 0.0 <= self.max_drawdown_p95 <= 1.0:
            raise ValueError("max_drawdown_p95 debe estar en el intervalo [0, 1]")


def _as_returns(values: Sequence[float]) -> tuple[float, ...]:
    out = tuple(float(v) for v in values)
    if not all(math.isfinite(v) for v in out):
        raise ValueError("returns debe contener solo números finitos")
    return out
