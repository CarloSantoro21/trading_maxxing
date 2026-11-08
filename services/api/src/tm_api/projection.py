"""Monte Carlo #3 (#38): probabilidad de alcanzar una meta antes de su fecha límite.

Proyecta el equity del usuario con lo que su bot haría con las estrategias que reciben
capital (``active`` y ``probation``), siguiendo la capa por usuario de la sección 3.4 de la
propuesta:

1. **Retornos diarios de cada estrategia** a tamaño completo (``k = 1``): los del portafolio
   de referencia (``reference_equity``) si ya tiene historia suficiente; si no, la curva
   out-of-sample del reporte del laboratorio (``equity_daily.strategy``).
2. **Peso del sleeve**: el mismo ``PortfolioAllocator`` que usa el ``BotRunner``.
3. **Exposición del nivel**: :func:`tm_agents.orchestrator.exposure.exposure_for_level` con
   el p95 y la curva de sensibilidad del reporte.
4. **Retorno diario del portafolio** = ``Σ peso * exposición * retorno`` (lo no invertido se
   queda en USDT, con retorno 0).
5. **Block bootstrap** (:func:`tm_agents.montecarlo.bootstrap`, bloques de 20 días) de esa
   serie con un horizonte igual a los días que faltan para la fecha límite.

La probabilidad principal es la de **tocar** el equity objetivo en algún día antes de la
fecha límite; también se devuelve la de terminar arriba de él en la fecha límite.

Todo es determinista (semilla fija): la misma meta con los mismos datos da la misma
respuesta. No hay red: los datos llegan ya leídos.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import Any

import numpy as np
from tm_agents.allocator import PortfolioAllocator
from tm_agents.montecarlo import bootstrap
from tm_agents.montecarlo.resampling import bootstrap_paths
from tm_agents.orchestrator.exposure import exposure_for_level, sensitivity_for
from tm_agents.registry import StrategyRecord
from tm_agents.signals import StrategySnapshot
from tm_agents.strategy_stats import StrategyState, StrategyStats
from tm_shared.contracts import Horizon

#: Días de historia del portafolio de referencia a partir de los cuales se usa en vez del
#: laboratorio (con menos, el bootstrap repetiría unas pocas semanas).
MIN_REFERENCE_DAYS = 60
#: Mínimo de retornos diarios para proyectar con una estrategia.
MIN_OBSERVATIONS = 30
#: Mismo bloque que el laboratorio (conserva la autocorrelación de unas semanas).
BLOCK_SIZE = 20
SEED = 42
MAX_SIMULATIONS = 2_000
MIN_SIMULATIONS = 500
#: Tope de celdas (simulaciones * días) para acotar la memoria de una petición.
CELL_BUDGET = 4_000_000
#: Horizonte máximo de una meta (10 años).
MAX_HORIZON_DAYS = 3_653
#: Puntos del abanico que se devuelven (se submuestrea el horizonte).
FAN_POINTS = 60
ANNUALIZATION = 365


@dataclass(frozen=True)
class StrategySeries:
    """Una estrategia que recibe capital, con su reporte y sus retornos diarios a k = 1."""

    name: str
    state: StrategyState
    lab_report: Mapping[str, Any] | None
    returns: Sequence[tuple[date, float]]
    source: str  # "reference" | "lab_report" | "none"
    report_source: str = "db"  # "db" | "file" | "none"


@dataclass(frozen=True)
class StrategyContribution:
    name: str
    state: str
    source: str
    report_source: str
    observations: int
    weight: float
    exposure: float
    excluded: str | None = None


@dataclass(frozen=True)
class FanPoint:
    day: date
    p10: float
    p50: float
    p90: float


@dataclass(frozen=True)
class GoalProjection:
    start_equity: float
    target_equity: float
    days: int
    probability: float
    probability_at_deadline: float
    invested_fraction: float
    n_simulations: int
    block_size: int
    seed: int
    final_equity_p10: float
    final_equity_p50: float
    final_equity_p90: float
    fan: list[FanPoint]
    strategies: list[StrategyContribution]
    notes: list[str] = field(default_factory=list)


class ProjectionError(ValueError):
    """La meta no se puede proyectar (fecha vencida o demasiado lejana)."""


# --- series -------------------------------------------------------------------------------


def returns_from_equity(points: Sequence[tuple[date, float]]) -> list[tuple[date, float]]:
    """Retornos diarios ``(día, equity_t / equity_{t-1} - 1)`` de una curva de equity.

    Ignora puntos no finitos o no positivos (no se puede dividir entre ellos).
    """
    clean = sorted((d, float(v)) for d, v in points if v is not None and math.isfinite(v) and v > 0)
    out: list[tuple[date, float]] = []
    for (_, prev), (day, value) in pairwise(clean):
        out.append((day, value / prev - 1.0))
    return out


def lab_equity_points(report: Mapping[str, Any] | None) -> list[tuple[date, float]]:
    """Curva ``equity_daily.strategy`` de un reporte del laboratorio (vacía si no la trae)."""
    if not report:
        return []
    raw = (report.get("equity_daily") or {}).get("strategy") or []
    points = []
    for item in raw:
        try:
            day, value = item[0], item[1]
            points.append((date.fromisoformat(str(day)[:10]), float(value)))
        except (TypeError, ValueError, IndexError):
            continue
    return points


def _align(series: Mapping[str, Sequence[tuple[date, float]]]) -> dict[str, np.ndarray]:
    """Alinea las series por fecha; si casi no se traslapan, por posición desde el final.

    Con fechas comunes se conserva la correlación entre estrategias. Si vienen de fuentes
    distintas (una del ledger de referencia y otra del laboratorio) las fechas no coinciden y
    se alinean los últimos ``n`` días de cada una.
    """
    if not series:
        return {}
    by_date = {name: dict(values) for name, values in series.items()}
    common = sorted(set.intersection(*(set(v) for v in by_date.values())))
    if len(common) >= MIN_OBSERVATIONS:
        return {name: np.array([v[d] for d in common], dtype=float) for name, v in by_date.items()}
    n = min(len(values) for values in series.values())
    return {
        name: np.array([r for _, r in sorted(values)[-n:]], dtype=float)
        for name, values in series.items()
    }


# --- estadísticas para el PortfolioAllocator -------------------------------------------------


def _finite_positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _stats(item: StrategySeries, returns: np.ndarray) -> tuple[StrategyStats, StrategySnapshot]:
    """Las estadísticas que ve el BotRunner (``StrategySnapshot``) con dos respaldos.

    Si el reporte no trae ``aggregate`` (formato plano del seed), la volatilidad y el Sharpe
    salen de la serie de retornos que se proyecta; así la estrategia no queda fuera solo
    porque su reporte está resumido.
    """
    record = StrategyRecord(name=item.name, state=item.state, lab_report=item.lab_report)
    snap = StrategySnapshot.from_record(record)
    std = float(returns.std(ddof=1)) if returns.size > 1 else 0.0
    vol = snap.volatility or std * math.sqrt(ANNUALIZATION)
    sharpe = snap.oos_sharpe
    if not sharpe:
        flat = _finite_positive((item.lab_report or {}).get("sharpe_oos"))
        series_sharpe = float(returns.mean()) / std * math.sqrt(ANNUALIZATION) if std > 0 else 0.0
        sharpe = flat if flat is not None else series_sharpe
    stats = StrategyStats(
        name=item.name,
        state=item.state,
        volatility=vol if math.isfinite(vol) else 0.0,
        oos_sharpe=sharpe if math.isfinite(sharpe) else 0.0,
        horizon=snap.horizon,
        max_drawdown_p95=snap.max_drawdown_p95,
    )
    return stats, snap


# --- proyección -----------------------------------------------------------------------------


def simulations_for(days: int) -> int:
    """Simulaciones para el horizonte, dentro del presupuesto de memoria."""
    return max(MIN_SIMULATIONS, min(MAX_SIMULATIONS, CELL_BUDGET // max(days, 1)))


def fan_indices(days: int, points: int = FAN_POINTS) -> list[int]:
    """Índices (días desde hoy, de 0 a ``days``) que se devuelven en el abanico."""
    if days + 1 <= points:
        return list(range(days + 1))
    return sorted({round(i * days / (points - 1)) for i in range(points)})


def project_goal(
    *,
    start_equity: float,
    target_equity: float,
    risk_level: int,
    horizon: Horizon,
    today: date,
    deadline: date,
    strategies: Sequence[StrategySeries],
    seed: int = SEED,
) -> GoalProjection:
    """Probabilidad de que el equity llegue a ``target_equity`` antes de ``deadline``."""
    days = (deadline - today).days
    if days <= 0:
        raise ProjectionError("La fecha límite de la meta ya pasó")
    if days > MAX_HORIZON_DAYS:
        raise ProjectionError("La fecha límite está a más de 10 años; no se proyecta")
    if not math.isfinite(start_equity) or start_equity <= 0:
        raise ProjectionError("El equity actual debe ser mayor que 0")

    notes: list[str] = []
    contributions: list[StrategyContribution] = []
    usable: dict[str, StrategySeries] = {}
    for item in strategies:
        if len(item.returns) < MIN_OBSERVATIONS:
            contributions.append(
                StrategyContribution(
                    name=item.name,
                    state=item.state.value,
                    source=item.source,
                    report_source=item.report_source,
                    observations=len(item.returns),
                    weight=0.0,
                    exposure=0.0,
                    excluded=(
                        f"sin historial suficiente ({len(item.returns)} días; "
                        f"se requieren {MIN_OBSERVATIONS})"
                    ),
                )
            )
        else:
            usable[item.name] = item

    aligned = _align({name: item.returns for name, item in usable.items()})
    stats: dict[str, StrategyStats] = {}
    snaps: dict[str, StrategySnapshot] = {}
    for name, item in usable.items():
        stats[name], snaps[name] = _stats(item, aligned[name])

    allocation = PortfolioAllocator().allocate_detailed(
        Decimal(str(round(start_equity, 2))), horizon, list(stats.values())
    )
    combined = np.zeros(len(next(iter(aligned.values()))) if aligned else 0, dtype=float)
    invested = 0.0
    for name, item in usable.items():
        snap = snaps[name]
        weight = float(allocation.weights.get(name, 0.0))
        exposure = exposure_for_level(
            risk_level, snap.max_drawdown_p95, sensitivity_for(snap.name, snap.sensitivity)
        )
        excluded = allocation.excluded.get(name)
        if excluded is None and exposure <= 0:
            excluded = "sin drawdown p95 del Monte Carlo: exposición 0"
        if excluded is None:
            combined += weight * exposure * aligned[name]
            invested += weight * exposure
        contributions.append(
            StrategyContribution(
                name=name,
                state=item.state.value,
                source=item.source,
                report_source=item.report_source,
                observations=int(aligned[name].size),
                weight=weight if excluded is None else 0.0,
                exposure=exposure,
                excluded=excluded,
            )
        )
    contributions.sort(key=lambda c: c.name)

    indices = fan_indices(days)
    days_out = [today + timedelta(days=i) for i in indices]
    if invested <= 0 or combined.size == 0:
        notes.append(
            "Ninguna estrategia recibe capital con tu nivel de riesgo: el equity se queda en "
            "USDT y no cambia."
        )
        reached = 1.0 if start_equity >= target_equity else 0.0
        return GoalProjection(
            start_equity=start_equity,
            target_equity=target_equity,
            days=days,
            probability=reached,
            probability_at_deadline=reached,
            invested_fraction=0.0,
            n_simulations=0,
            block_size=0,
            seed=seed,
            final_equity_p10=start_equity,
            final_equity_p50=start_equity,
            final_equity_p90=start_equity,
            fan=[FanPoint(d, start_equity, start_equity, start_equity) for d in days_out],
            strategies=contributions,
            notes=notes,
        )

    if any(c.source == "lab_report" for c in contributions if c.excluded is None):
        notes.append(
            "Al menos una estrategia aún no tiene historia suficiente en su portafolio de "
            "referencia: se usaron sus retornos out-of-sample del laboratorio."
        )
    n_sim = simulations_for(days)
    block = min(BLOCK_SIZE, combined.size)
    report = bootstrap(
        combined,
        n_sim,
        seed=seed,
        block_size=block,
        initial_capital=start_equity,
        horizon=days,
    )
    # Mismas trayectorias que el reporte (misma semilla y mismo orden de sorteos) para medir
    # si el equity toca la meta en algún día, no solo al final.
    paths = bootstrap_paths(combined, n_sim, np.random.default_rng(seed), block, length=days)
    peak_growth = np.cumprod(1.0 + paths, axis=1).max(axis=1)
    touched = np.maximum(peak_growth, 1.0) * start_equity >= target_equity
    final = start_equity * (1.0 + report.final_returns)

    fan = report.equity_fan
    return GoalProjection(
        start_equity=start_equity,
        target_equity=target_equity,
        days=days,
        probability=float(touched.mean()),
        probability_at_deadline=float((final >= target_equity).mean()),
        invested_fraction=invested,
        n_simulations=n_sim,
        block_size=block,
        seed=seed,
        final_equity_p10=float(np.percentile(final, 10)),
        final_equity_p50=float(np.percentile(final, 50)),
        final_equity_p90=float(np.percentile(final, 90)),
        fan=[
            FanPoint(d, float(fan.p10[i]), float(fan.p50[i]), float(fan.p90[i]))
            for d, i in zip(days_out, indices, strict=True)
        ],
        strategies=contributions,
        notes=notes,
    )


__all__ = [
    "MIN_OBSERVATIONS",
    "MIN_REFERENCE_DAYS",
    "FanPoint",
    "GoalProjection",
    "ProjectionError",
    "StrategyContribution",
    "StrategySeries",
    "fan_indices",
    "lab_equity_points",
    "project_goal",
    "returns_from_equity",
    "simulations_for",
]
