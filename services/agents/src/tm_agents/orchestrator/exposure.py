"""Exposición de cada sleeve según el nivel de riesgo del usuario (propuesta, sección 3.4).

La regla original es ``exposición = min(1, drawdown_del_nivel / drawdown_p95)``, que supone
que el drawdown escala en proporción a lo invertido. El laboratorio mostró que no es así: la
capitalización compuesta hace que el drawdown baje **menos** que en proporción al reducir la
exposición. Con el criterio a 4 años (PR #80) invertir 33 % del TrendAgent daba un p95 de
25.2 %, no de 20 %. Con el criterio vigente, el p95 a 1 año (#83), el efecto es menor pero
sigue ahí: con 40 % invertido el p95 es 1.13 veces el proporcional y con 10 %, 1.19 veces.

Por eso la exposición se calcula así:

1. **Con curva de sensibilidad** (pares ``(exposición k, drawdown p95 con k)``): se busca, por
   interpolación lineal, la ``k`` cuyo p95 relativo (``p95(k) / p95(1)``) iguala
   ``drawdown_del_nivel / p95``. La curva se usa como forma relativa, así sigue sirviendo si
   el p95 del reporte se actualiza. Las curvas de ``trend`` y ``trend_vol`` se midieron con
   :func:`measure_sensitivity` sobre los retornos diarios out-of-sample de sus reportes
   (``docs/estrategias/reportes/``) con el mismo block bootstrap del laboratorio (bloques de
   20 días, 10,000 simulaciones, semilla 42, horizonte de 365 días); reproducen el p95 de
   cada reporte con k = 1. Un reporte nuevo puede traer la suya en
   ``lab_report["exposure_sensitivity"]``.
2. **Sin curva**: ``min(1, drawdown_del_nivel / (p95 * CONSERVATIVE_DD_FACTOR))`` con un factor
   de 1.3, mayor que el peor medido (1.19 a 1 año y 1.27 a 4 años), así que nunca invierte
   más que una curva medida.

Una estrategia sin p95 (sin Monte Carlo) recibe exposición 0.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from itertools import pairwise
from types import MappingProxyType

#: Drawdown máximo tolerado por nivel de riesgo (tabla de la sección 3.4).
LEVEL_MAX_DRAWDOWN: Mapping[int, float] = MappingProxyType(
    {1: 0.10, 2: 0.15, 3: 0.20, 4: 0.30, 5: 0.40}
)

#: Factor conservador cuando no hay curva: el p95 con exposición k se toma como 1.3 k p95.
CONSERVATIVE_DD_FACTOR = 1.3

#: Exposiciones en las que :func:`measure_sensitivity` mide la curva.
SENSITIVITY_GRID: tuple[float, ...] = (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1)

#: Sensibilidad medida del TrendAgent sin vol targeting (reportes/trend.json, 2021-2025,
#: drawdown p95 a 1 año).
TREND_SENSITIVITY: tuple[tuple[float, float], ...] = (
    (1.0, 0.4383),
    (0.9, 0.4023),
    (0.8, 0.3650),
    (0.7, 0.3257),
    (0.6, 0.2847),
    (0.5, 0.2418),
    (0.4, 0.1975),
    (0.3, 0.1510),
    (0.2, 0.1025),
    (0.1, 0.0522),
)

#: Sensibilidad medida del TrendAgent con vol targeting (reportes/trend_vol.json).
TREND_VOL_SENSITIVITY: tuple[tuple[float, float], ...] = (
    (1.0, 0.3561),
    (0.9, 0.3254),
    (0.8, 0.2938),
    (0.7, 0.2609),
    (0.6, 0.2272),
    (0.5, 0.1923),
    (0.4, 0.1563),
    (0.3, 0.1191),
    (0.2, 0.0807),
    (0.1, 0.0410),
)

#: Curvas conocidas por nombre del reporte (``config.strategy``); se usan si el reporte no
#: trae la suya.
KNOWN_SENSITIVITY: Mapping[str, tuple[tuple[float, float], ...]] = MappingProxyType(
    {"trend": TREND_SENSITIVITY, "trend_vol": TREND_VOL_SENSITIVITY}
)


def level_drawdown(risk_level: int) -> float:
    try:
        return LEVEL_MAX_DRAWDOWN[int(risk_level)]
    except (KeyError, TypeError, ValueError):
        raise ValueError("risk_level debe estar entre 1 y 5") from None


def exposure_for_level(
    risk_level: int,
    p95: float | None,
    sensitivity: Sequence[tuple[float, float]] | None = None,
) -> float:
    """Fracción del sleeve que invierte un bot de ``risk_level`` (0 a 1)."""
    target = level_drawdown(risk_level)
    if p95 is None or not math.isfinite(p95) or p95 <= 0:
        return 0.0
    ratio = target / p95
    if ratio >= 1.0:
        return 1.0
    curve = _relative_curve(sensitivity) if sensitivity else None
    if curve is None:
        return max(0.0, min(1.0, ratio / CONSERVATIVE_DD_FACTOR))
    return _invert(curve, ratio)


def sensitivity_for(
    name: str, reported: Sequence[tuple[float, float]] | None
) -> Sequence[tuple[float, float]] | None:
    """La curva del reporte si la trae; si no, la conocida para la estrategia (o ninguna)."""
    return reported or KNOWN_SENSITIVITY.get(name)


def measure_sensitivity(
    daily_returns: Sequence[float],
    exposures: Sequence[float] = SENSITIVITY_GRID,
    *,
    n_simulations: int = 10_000,
    block_size: int = 20,
    seed: int = 42,
    horizon: int | None = None,
) -> tuple[tuple[float, float], ...]:
    """Curva ``(k, drawdown p95)`` escalando los retornos diarios por ``k``.

    Es el método de la sección de sensibilidad del laboratorio: mismo block bootstrap
    (``tm_agents.montecarlo.bootstrap``) para cada ``k``; ignora que la comisión también
    escala. ``horizon`` por omisión es el del criterio (365 días).
    """
    import numpy as np

    from tm_agents.montecarlo import ONE_YEAR_DAYS, bootstrap

    returns = np.asarray(daily_returns, dtype=float)
    steps = ONE_YEAR_DAYS if horizon is None else horizon
    out = []
    for k in exposures:
        report = bootstrap(
            returns * float(k), n_simulations, seed=seed, block_size=block_size, horizon=steps
        )
        out.append((float(k), float(report.max_drawdown.p95)))
    return tuple(out)


def _relative_curve(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]] | None:
    """Curva ``(k, p95(k) / p95(1))`` ordenada por k, con el origen; None si no sirve."""
    clean = sorted(
        (float(k), float(d))
        for k, d in points
        if math.isfinite(k) and math.isfinite(d) and 0 < k <= 1 and d > 0
    )
    full = [d for k, d in clean if k == 1.0]
    if not full:
        return None
    base = full[0]
    curve = [(0.0, 0.0), *((k, d / base) for k, d in clean)]
    # Debe crecer con la exposición; si no, no se puede invertir con confianza.
    if any(b[1] < a[1] for a, b in pairwise(curve)):
        return None
    return curve


def _invert(curve: list[tuple[float, float]], ratio: float) -> float:
    for (k0, r0), (k1, r1) in pairwise(curve):
        if r0 <= ratio <= r1:
            if r1 == r0:
                return k0
            return k0 + (ratio - r0) * (k1 - k0) / (r1 - r0)
    return 1.0


__all__ = [
    "CONSERVATIVE_DD_FACTOR",
    "KNOWN_SENSITIVITY",
    "LEVEL_MAX_DRAWDOWN",
    "SENSITIVITY_GRID",
    "TREND_SENSITIVITY",
    "TREND_VOL_SENSITIVITY",
    "exposure_for_level",
    "level_drawdown",
    "measure_sensitivity",
    "sensitivity_for",
]
