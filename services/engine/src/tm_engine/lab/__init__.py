"""Laboratorio de validación (BacktestService, #13): walk-forward, holdout y Monte Carlo.

    from tm_engine.lab import WalkForwardConfig, walk_forward

    report = walk_forward(WalkForwardConfig(strategy="trend", data_dir="data/bars"))
    report.aggregate["sharpe"], report.approval["approved"]

Ver `docs/estrategias/criterios-de-aprobacion.md` y `services/engine/README.md`.
"""

from tm_engine.lab.config import HoldoutError, WalkForwardConfig, Window
from tm_engine.lab.walkforward import WalkForwardReport, walk_forward

__all__ = ["HoldoutError", "WalkForwardConfig", "WalkForwardReport", "Window", "walk_forward"]
