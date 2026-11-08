"""MonteCarloService del laboratorio: remuestreo de trades y criterios de aprobación.

Toma los retornos fraccionales por trade de un backtest, genera miles de
secuencias alternativas (``reshuffle`` sin reemplazo o ``bootstrap`` con
reemplazo, opcionalmente por bloques) y resume la distribución del drawdown
máximo, del retorno final y la probabilidad de ruina. Con :func:`evaluate` se
aplican los criterios fijos de aprobación de la propuesta (sección 3.2, fase 1).

Ejemplo::

    import numpy as np
    from tm_agents.montecarlo import ApprovalCriteria, bootstrap, evaluate, reshuffle

    trades = np.random.default_rng(0).normal(0.004, 0.02, size=300)

    report = reshuffle(trades, 10_000, seed=42, initial_capital=10_000, ruin_level=0.7)
    report.max_drawdown.p95        # drawdown máximo del percentil 95 (fracción)
    report.probability_of_ruin     # P(equity < 70 % del capital en algún punto)
    report.equity_fan.p50          # mediana del equity por paso (n_trades + 1)

    # Block bootstrap para conservar la autocorrelación de corto plazo
    report_bb = bootstrap(trades, 10_000, seed=42, block_size=10)

    result = evaluate(report, oos_sharpe=0.8, n_trades=trades.size,
                      criteria=ApprovalCriteria(max_drawdown_p95=0.25))
    result.approved                # True si cumple los tres criterios
    result.reasons                 # razones legibles de cada criterio que falla

    # Criterio del laboratorio: drawdown p95 en un horizonte de 1 año (365 días)
    daily = np.random.default_rng(1).normal(0.001, 0.02, size=1461)
    one_year = bootstrap(daily, 10_000, seed=42, block_size=20, horizon=ONE_YEAR_DAYS)
    evaluate(one_year, oos_sharpe=0.8, n_trades=150,
             criteria=ApprovalCriteria(max_drawdown_p95=0.40, drawdown_horizon=ONE_YEAR_DAYS))
"""

from tm_agents.montecarlo.approval import (
    ONE_YEAR_DAYS,
    ApprovalCriteria,
    ApprovalResult,
    CriterionCheck,
    evaluate,
)
from tm_agents.montecarlo.report import DistributionSummary, EquityFan, MCReport
from tm_agents.montecarlo.simulation import bootstrap, reshuffle, simulate

__all__ = [
    "ONE_YEAR_DAYS",
    "ApprovalCriteria",
    "ApprovalResult",
    "CriterionCheck",
    "DistributionSummary",
    "EquityFan",
    "MCReport",
    "bootstrap",
    "evaluate",
    "reshuffle",
    "simulate",
]
