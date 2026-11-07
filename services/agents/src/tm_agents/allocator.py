"""PortfolioAllocator: reparte el capital del perfil entre los sleeves de las estrategias.

Cada estrategia que opera con capital tiene su propio bolsillo (sleeve). El peso de cada
sleeve sale de tres factores (propuesta, secciones 2.1 y 3.3):

1. **Inversa de la volatilidad validada**: las estrategias más estables reciben más.
2. **Penalización por correlación**: el peso se divide entre ``1 + k * rho``, donde ``rho`` es
   la correlación positiva promedio con las demás estrategias elegibles. Dos estrategias que
   se mueven igual se reparten el capital que una sola, y una estrategia descorrelacionada
   gana peso relativo.
3. **Peso por horizonte**: un perfil de corto plazo favorece las estrategias intradía y uno
   de largo plazo las de tendencia diaria (tabla configurable).

Después se aplica el estado del ciclo de vida: ``active`` recibe su peso completo,
``probation`` (en observación) la mitad, y ``research``, ``lab``, ``shadow`` y ``retired``
reciben 0. Lo que libera una estrategia en observación se reparte entre las activas; si no
hay ninguna activa, se queda sin asignar (en USDT).

Por último se respeta el nocional mínimo de Binance: un sleeve que no lo alcanza se elimina
y su capital se redistribuye. Los montos se redondean hacia abajo al ``quantum`` (0.01 USDT)
y los centavos sobrantes se reparten por el método de residuos mayores, de modo que la suma
de los sleeves más lo no asignado es exactamente el capital del perfil.

Ejemplo::

    from decimal import Decimal
    from tm_shared.contracts import Horizon
    from tm_agents.allocator import PortfolioAllocator
    from tm_agents.strategy_stats import StrategyState, StrategyStats

    stats = [
        StrategyStats("trend", StrategyState.ACTIVE, volatility=0.30, oos_sharpe=0.9),
        StrategyStats("kronos", StrategyState.PROBATION, volatility=0.45, oos_sharpe=0.7),
        StrategyStats("momentum", StrategyState.SHADOW, volatility=0.25, oos_sharpe=1.1),
    ]
    PortfolioAllocator().allocate(Decimal("1000"), Horizon.MEDIUM, stats)
    # {"trend": Decimal("...") , "kronos": Decimal("..."), "momentum": Decimal("0")}
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal
from types import MappingProxyType

import numpy as np
import pandas as pd
from tm_shared.contracts import Horizon

from tm_agents.montecarlo import ApprovalCriteria
from tm_agents.strategy_stats import StrategyState, StrategyStats

# Matriz de correlación: DataFrame cuadrado indexado por nombre o {a: {b: rho}}
Correlation = pd.DataFrame | Mapping[str, Mapping[str, float]]

# Perfil -> horizonte de la estrategia -> multiplicador del peso
DEFAULT_HORIZON_WEIGHTS: Mapping[Horizon, Mapping[Horizon, float]] = MappingProxyType(
    {
        Horizon.SHORT: {Horizon.SHORT: 1.0, Horizon.MEDIUM: 0.75, Horizon.LONG: 0.5},
        Horizon.MEDIUM: {Horizon.SHORT: 0.75, Horizon.MEDIUM: 1.0, Horizon.LONG: 0.75},
        Horizon.LONG: {Horizon.SHORT: 0.5, Horizon.MEDIUM: 0.75, Horizon.LONG: 1.0},
    }
)

# Multiplicador del peso por estado del ciclo de vida
STATE_MULTIPLIER: Mapping[StrategyState, float] = MappingProxyType(
    {StrategyState.ACTIVE: 1.0, StrategyState.PROBATION: 0.5}
)

# Mínimo de observaciones alineadas para estimar una correlación a partir de retornos
MIN_CORRELATION_OBS = 3


@dataclass(frozen=True)
class AllocationResult:
    """Detalle de una asignación.

    Attributes:
        sleeves: capital por estrategia (todas las recibidas; 0 si quedó fuera).
        unallocated: capital que no se asignó (observación sin activas, nocional mínimo o
            centavos que no caben en el ``quantum``). ``sum(sleeves) + unallocated`` es
            igual al capital.
        weights: fracción efectiva del capital de cada estrategia con capital.
        excluded: razón legible de cada estrategia que recibió 0.
    """

    sleeves: dict[str, Decimal]
    unallocated: Decimal
    weights: dict[str, float] = field(default_factory=dict)
    excluded: dict[str, str] = field(default_factory=dict)


class PortfolioAllocator:
    """Calcula el capital de cada sleeve. Determinista y sin estado entre llamadas.

    Args:
        horizon_weights: tabla perfil → horizonte de la estrategia → multiplicador.
            Un horizonte que falte en la tabla cuenta como 1.
        correlation_penalty: intensidad ``k`` de la penalización por correlación
            (0 la desactiva).
        min_notional: nocional mínimo por sleeve en USDT (igual que ``RiskLimits``).
        quantum: unidad de redondeo de los montos.
        min_oos_sharpe: una estrategia con Sharpe OOS menor o igual no recibe capital.
    """

    def __init__(
        self,
        *,
        horizon_weights: Mapping[Horizon, Mapping[Horizon, float]] = DEFAULT_HORIZON_WEIGHTS,
        correlation_penalty: float = 1.0,
        min_notional: Decimal = Decimal("10"),
        quantum: Decimal = Decimal("0.01"),
        min_oos_sharpe: float = 0.0,
    ) -> None:
        if not math.isfinite(correlation_penalty) or correlation_penalty < 0:
            raise ValueError("correlation_penalty debe ser un número finito mayor o igual a 0")
        if min_notional < 0:
            raise ValueError("min_notional debe ser mayor o igual a 0")
        if quantum <= 0:
            raise ValueError("quantum debe ser mayor a 0")
        for profile_h, table in horizon_weights.items():
            for strategy_h, w in table.items():
                if not math.isfinite(w) or w < 0:
                    raise ValueError(
                        f"peso de horizonte inválido para {profile_h.value}/{strategy_h.value}"
                    )
        self.horizon_weights = horizon_weights
        self.correlation_penalty = correlation_penalty
        self.min_notional = min_notional
        self.quantum = quantum
        self.min_oos_sharpe = min_oos_sharpe

    # --- API ------------------------------------------------------------------------------
    def allocate(
        self,
        profile_capital: Decimal,
        horizon: Horizon,
        strategies: Sequence[StrategyStats],
        *,
        correlation: Correlation | None = None,
        max_drawdown: float | Decimal | None = None,
    ) -> dict[str, Decimal]:
        """Capital por sleeve (todas las estrategias recibidas, 0 si no reciben capital)."""
        return self.allocate_detailed(
            profile_capital,
            horizon,
            strategies,
            correlation=correlation,
            max_drawdown=max_drawdown,
        ).sleeves

    def allocate_detailed(
        self,
        profile_capital: Decimal,
        horizon: Horizon,
        strategies: Sequence[StrategyStats],
        *,
        correlation: Correlation | None = None,
        max_drawdown: float | Decimal | None = None,
    ) -> AllocationResult:
        """Como :meth:`allocate`, pero con los pesos, lo no asignado y las exclusiones.

        Args:
            profile_capital: capital del perfil en USDT.
            horizon: horizonte del perfil.
            strategies: estadísticas de cada estrategia (nombres únicos).
            correlation: matriz de correlación de retornos opcional. Los pares que falten
                se estiman con ``StrategyStats.returns`` si ambas los traen; si no, rho = 0.
            max_drawdown: drawdown máximo del perfil. Si se indica, una estrategia cuyo
                drawdown p95 del Monte Carlo no quede por debajo
                (``ApprovalCriteria.for_profile``) no recibe capital.
        """
        capital = Decimal(profile_capital)
        if not capital.is_finite() or capital < 0:
            raise ValueError("profile_capital debe ser un número finito mayor o igual a 0")
        names = [s.name for s in strategies]
        if len(set(names)) != len(names):
            raise ValueError("los nombres de las estrategias deben ser únicos")

        dd_limit = (
            ApprovalCriteria.for_profile(max_drawdown).max_drawdown_p95
            if max_drawdown is not None
            else None
        )
        excluded: dict[str, str] = {}
        base: dict[str, float] = {}
        by_name = {s.name: s for s in strategies}
        for s in strategies:
            reason = self._exclusion_reason(s, horizon, dd_limit)
            if reason is None:
                base[s.name] = self._horizon_weight(horizon, s) / s.volatility
            else:
                excluded[s.name] = reason

        corr = _CorrelationLookup(correlation, by_name)
        weights: dict[str, float] = {}
        while base:
            weights = self._effective_weights(base, by_name, corr)
            below = {n: w for n, w in weights.items() if capital * Decimal(w) < self.min_notional}
            if not below:
                break
            # Se quita el sleeve más pequeño y se recalcula: su capital pasa a los demás
            smallest = min(below, key=lambda n: (below[n], n))
            excluded[smallest] = (
                f"su sleeve ({capital * Decimal(below[smallest]):.2f} USDT) no alcanza el "
                f"nocional mínimo de {self.min_notional} USDT"
            )
            del base[smallest]
            weights = {}

        amounts = self._round(capital, weights)
        sleeves = {n: amounts.get(n, Decimal("0")) for n in names}
        return AllocationResult(
            sleeves=sleeves,
            unallocated=capital - sum(sleeves.values(), Decimal("0")),
            weights=weights,
            excluded=excluded,
        )

    # --- pasos internos -------------------------------------------------------------------
    def _exclusion_reason(
        self, s: StrategyStats, horizon: Horizon, dd_limit: float | None
    ) -> str | None:
        if not s.state.gets_capital:
            return f"estado {s.state.value}: no opera con capital"
        if not math.isfinite(s.volatility) or s.volatility <= 0:
            return "volatilidad validada no positiva o no finita"
        if not math.isfinite(s.oos_sharpe) or s.oos_sharpe <= self.min_oos_sharpe:
            return (
                f"Sharpe out-of-sample de {s.oos_sharpe:.2f} "
                f"(se requiere mayor a {self.min_oos_sharpe:.2f})"
            )
        dd_p95 = s.max_drawdown_p95
        if dd_limit is not None and dd_p95 is not None and dd_p95 >= dd_limit:
            return f"drawdown p95 de {dd_p95:.1%} (el perfil tolera menos de {dd_limit:.1%})"
        if self._horizon_weight(horizon, s) <= 0:
            return f"peso 0 para el horizonte {horizon.value}"
        return None

    def _horizon_weight(self, horizon: Horizon, s: StrategyStats) -> float:
        if s.horizon is None:
            return 1.0
        return float(self.horizon_weights.get(horizon, {}).get(s.horizon, 1.0))

    def _effective_weights(
        self,
        base: Mapping[str, float],
        by_name: Mapping[str, StrategyStats],
        corr: _CorrelationLookup,
    ) -> dict[str, float]:
        """Fracción del capital de cada sleeve, ya con correlación y estado."""
        names = sorted(base)
        raw: dict[str, float] = {}
        for n in names:
            others = [m for m in names if m != n]
            avg_rho = sum(max(0.0, corr.get(n, m)) for m in others) / len(others) if others else 0.0
            raw[n] = base[n] / (1.0 + self.correlation_penalty * avg_rho)

        total = sum(raw.values())
        shares = {n: raw[n] / total for n in names}
        weights = {n: shares[n] * STATE_MULTIPLIER[by_name[n].state] for n in names}

        # Lo que libera la observación se reparte entre las activas, en proporción a su peso
        freed = 1.0 - sum(weights.values())
        active = [n for n in names if by_name[n].state is StrategyState.ACTIVE]
        active_share = sum(shares[n] for n in active)
        if freed > 0 and active_share > 0:
            for n in active:
                weights[n] += freed * shares[n] / active_share
        return weights

    def _round(self, capital: Decimal, weights: Mapping[str, float]) -> dict[str, Decimal]:
        """Redondea hacia abajo al quantum y reparte los sobrantes por residuos mayores."""
        if not weights:
            return {}
        units_total = int((capital / self.quantum).to_integral_value(rounding=ROUND_DOWN))
        fraction = min(1.0, sum(weights.values()))
        # Unidades a repartir: el total si todo se asigna, o la fracción (observación sola)
        target = units_total if fraction >= 1.0 - 1e-12 else math.floor(units_total * fraction)
        ideal = {n: units_total * w for n, w in weights.items()}
        units = {n: math.floor(v) for n, v in ideal.items()}
        leftover = target - sum(units.values())
        for n in sorted(ideal, key=lambda n: (-(ideal[n] - units[n]), n))[: max(0, leftover)]:
            units[n] += 1
        return {n: (self.quantum * u).quantize(self.quantum) for n, u in units.items()}


class _CorrelationLookup:
    """Correlación entre dos estrategias: matriz explícita o estimada con sus retornos."""

    def __init__(
        self, correlation: Correlation | None, by_name: Mapping[str, StrategyStats]
    ) -> None:
        if isinstance(correlation, pd.DataFrame):
            correlation = {
                str(col): {str(row): float(v) for row, v in correlation[col].items()}
                for col in correlation.columns
            }
        self._matrix = correlation or {}
        self._by_name = by_name
        self._cache: dict[tuple[str, str], float] = {}

    def get(self, a: str, b: str) -> float:
        key = (a, b) if a <= b else (b, a)
        if key not in self._cache:
            self._cache[key] = self._lookup(a, b)
        return self._cache[key]

    def _lookup(self, a: str, b: str) -> float:
        for x, y in ((a, b), (b, a)):
            rho = self._matrix.get(x, {}).get(y)
            if rho is not None and math.isfinite(rho):
                return max(-1.0, min(1.0, float(rho)))
        return _returns_correlation(self._by_name[a].returns, self._by_name[b].returns)


def _returns_correlation(ra: Sequence[float] | None, rb: Sequence[float] | None) -> float:
    """Correlación de Pearson de los últimos retornos alineados (0 si no se puede estimar)."""
    if ra is None or rb is None:
        return 0.0
    n = min(len(ra), len(rb))
    if n < MIN_CORRELATION_OBS:
        return 0.0
    a = np.asarray(ra[-n:], dtype=np.float64)
    b = np.asarray(rb[-n:], dtype=np.float64)
    if a.std() == 0 or b.std() == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])
