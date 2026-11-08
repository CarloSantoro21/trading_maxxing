"""Exposición de cada sleeve según el nivel de riesgo del usuario (propuesta, sección 3.4).

La regla original es ``exposición = min(1, drawdown_del_nivel / drawdown_p95)``, que supone
que el drawdown escala en proporción a lo invertido. El laboratorio mostró que no es así
(`docs/estrategias/resultados-trend.md`, "Sensibilidad: exposición reducida"): con el mismo
block bootstrap, invertir 33 % del sleeve del TrendAgent da un p95 de 25.2 %, no de 20 %; para
llegar a 20 % hace falta ~25 %. La capitalización compuesta hace que el drawdown baje menos que
en proporción.

Por eso la exposición se calcula así:

1. **Con curva de sensibilidad** (pares ``(exposición k, drawdown p95 con k)`` del
   laboratorio): se busca, por interpolación lineal, la ``k`` cuyo p95 relativo
   (``p95(k) / p95(1)``) iguala ``drawdown_del_nivel / p95``. La curva se usa como forma
   relativa, así sigue sirviendo si el p95 del reporte se actualiza. La del TrendAgent sale
   de la tabla del reporte (:data:`TREND_SENSITIVITY`); un reporte nuevo puede traer la suya
   en ``lab_report["exposure_sensitivity"]``.
2. **Sin curva**: ``min(1, drawdown_del_nivel / (p95 * CONSERVATIVE_DD_FACTOR))`` con un factor
   de 1.3. Es la pendiente de la curva del TrendAgent en exposiciones bajas (19.6 / 61.5 / 0.25
   ≈ 1.27 y 25.2 / 61.5 / 0.33 ≈ 1.24), así que nunca invierte más que la curva medida.

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

#: Sensibilidad medida del TrendAgent (resultados-trend.md, walk-forward 2021-2024).
TREND_SENSITIVITY: tuple[tuple[float, float], ...] = (
    (1.00, 0.615),
    (0.65, 0.448),
    (0.60, 0.420),
    (0.50, 0.362),
    (0.33, 0.252),
    (0.25, 0.196),
)

#: Curvas conocidas por estrategia (se usan si el reporte no trae la suya).
KNOWN_SENSITIVITY: Mapping[str, tuple[tuple[float, float], ...]] = MappingProxyType(
    {"trend": TREND_SENSITIVITY}
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
    "TREND_SENSITIVITY",
    "exposure_for_level",
    "level_drawdown",
    "sensitivity_for",
]
