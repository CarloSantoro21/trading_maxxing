"""Criterios fijos de aprobación de estrategias (propuesta, sección 3.2, fase 1)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from tm_agents.montecarlo.report import MCReport


@dataclass(frozen=True, slots=True)
class ApprovalCriteria:
    """Umbrales que una estrategia debe cumplir para recibir capital.

    Attributes:
        min_oos_sharpe: el Sharpe out-of-sample debe ser estrictamente mayor.
        max_drawdown_p95: el drawdown máximo del percentil 95 debe ser
            estrictamente menor (fracción, 0.20 = 20 %).
        min_trades: número mínimo de trades del backtest (inclusive).
        low_freq_min_trades: alternativa para estrategias de baja frecuencia
            (p. ej. trend following con trades de semanas o meses): basta con
            este número de trades si además hay ``low_freq_min_oos_years`` años
            out-of-sample con exposición diaria.
        low_freq_min_oos_years: años out-of-sample requeridos por la alternativa.
    """

    min_oos_sharpe: float = 0.5
    max_drawdown_p95: float = 0.20
    min_trades: int = 100
    low_freq_min_trades: int = 30
    low_freq_min_oos_years: float = 3.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.min_oos_sharpe):
            raise ValueError("min_oos_sharpe debe ser finito")
        if not 0.0 < self.max_drawdown_p95 <= 1.0:
            raise ValueError("max_drawdown_p95 debe estar en el intervalo (0, 1]")
        if self.min_trades < 1:
            raise ValueError("min_trades debe ser mayor o igual a 1")
        if not 1 <= self.low_freq_min_trades <= self.min_trades:
            raise ValueError("low_freq_min_trades debe estar entre 1 y min_trades")
        if not self.low_freq_min_oos_years > 0:
            raise ValueError("low_freq_min_oos_years debe ser positivo")

    @classmethod
    def for_profile(
        cls,
        max_drawdown: float | Decimal,
        *,
        min_oos_sharpe: float = 0.5,
        min_trades: int = 100,
    ) -> ApprovalCriteria:
        """Criterios cuyo límite de drawdown p95 es el ``max_drawdown`` del perfil.

        Así una estrategia solo se aprueba (y recibe capital) si en el 95 % de las
        simulaciones su drawdown queda por debajo de lo que el usuario tolera.

        Args:
            max_drawdown: drawdown máximo del perfil como fracción (0.20 = 20 %),
                tal como lo guarda ``profiles.max_drawdown`` y ``RiskLimits``.
        """
        return cls(
            min_oos_sharpe=min_oos_sharpe,
            max_drawdown_p95=float(max_drawdown),
            min_trades=min_trades,
        )


@dataclass(frozen=True, slots=True)
class CriterionCheck:
    """Resultado de un criterio individual."""

    name: str
    passed: bool
    value: float
    threshold: float
    reason: str


@dataclass(frozen=True, slots=True)
class ApprovalResult:
    """Veredicto de aprobación con el detalle por criterio."""

    approved: bool
    checks: tuple[CriterionCheck, ...]

    @property
    def reasons(self) -> tuple[str, ...]:
        """Razones legibles de cada criterio que falló (vacío si se aprueba)."""
        return tuple(c.reason for c in self.checks if not c.passed)


def evaluate(
    report: MCReport,
    oos_sharpe: float,
    n_trades: int,
    criteria: ApprovalCriteria | None = None,
    oos_years: float | None = None,
) -> ApprovalResult:
    """Aplica los criterios de aprobación a un reporte de Monte Carlo.

    ``oos_years`` (años out-of-sample con exposición diaria) habilita la
    alternativa de baja frecuencia del criterio de evidencia: al menos
    ``low_freq_min_trades`` trades y ``low_freq_min_oos_years`` años.
    """
    criteria = criteria or ApprovalCriteria()
    dd_p95 = report.max_drawdown.p95

    sharpe_ok = math.isfinite(oos_sharpe) and oos_sharpe > criteria.min_oos_sharpe
    dd_ok = dd_p95 < criteria.max_drawdown_p95
    low_freq_ok = (
        oos_years is not None
        and n_trades >= criteria.low_freq_min_trades
        and oos_years >= criteria.low_freq_min_oos_years
    )
    trades_ok = n_trades >= criteria.min_trades or low_freq_ok
    years_txt = f", {oos_years:.1f} años out-of-sample" if oos_years is not None else ""

    checks = (
        CriterionCheck(
            name="oos_sharpe",
            passed=sharpe_ok,
            value=float(oos_sharpe),
            threshold=criteria.min_oos_sharpe,
            reason=(
                f"Sharpe out-of-sample de {oos_sharpe:.2f} "
                f"(se requiere mayor a {criteria.min_oos_sharpe:.2f})"
            ),
        ),
        CriterionCheck(
            name="max_drawdown_p95",
            passed=dd_ok,
            value=dd_p95,
            threshold=criteria.max_drawdown_p95,
            reason=(
                f"Drawdown máximo p95 de {dd_p95:.1%} "
                f"(se requiere menor a {criteria.max_drawdown_p95:.1%})"
            ),
        ),
        CriterionCheck(
            name="min_trades",
            passed=trades_ok,
            value=float(n_trades),
            threshold=float(criteria.min_trades),
            reason=(
                f"{n_trades} trades{years_txt} (se requieren al menos {criteria.min_trades}, "
                f"o {criteria.low_freq_min_trades} con {criteria.low_freq_min_oos_years:g} "
                "años out-of-sample)"
            ),
        ),
    )
    return ApprovalResult(approved=all(c.passed for c in checks), checks=checks)
