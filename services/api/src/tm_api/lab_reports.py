"""Reportes del laboratorio de walk-forward (PR #80) para la página /backtests (#24).

El laboratorio (``python -m tm_engine.lab``) escribe un JSON por estrategia en
``docs/estrategias/reportes/``. Este módulo los lee del directorio ``LAB_REPORTS_DIR`` (por
omisión, el del repo) y arma lo que necesita el frontend:

- el resumen de cada reporte (métricas out-of-sample, comprar y mantener y el veredicto);
- el detalle: curva de equity out-of-sample contra comprar y mantener, la tabla por ventana
  del walk-forward y el histograma del drawdown máximo del Monte Carlo.

El reporte solo guarda los percentiles del drawdown, no las 10,000 muestras. Para el
histograma se repite el mismo block bootstrap del laboratorio
(:func:`tm_agents.montecarlo.bootstrap` sobre los retornos diarios out-of-sample, con el
número de simulaciones, el bloque, la semilla y el horizonte del reporte), así que su p95
coincide con el del reporte. El resultado se guarda en memoria por archivo y fecha de
modificación.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from tm_agents.montecarlo import ONE_YEAR_DAYS, bootstrap

from tm_api.projection import lab_equity_points, returns_from_equity

#: Nombre de un reporte (el del archivo sin ``.json``): evita salirse del directorio.
REPORT_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
HISTOGRAM_BINS = 30

# Directorio del repo: services/api/src/tm_api/lab_reports.py -> parents[4].
_REPO_REPORTS = Path(__file__).resolve().parents[4] / "docs" / "estrategias" / "reportes"


class ReportNotFound(LookupError):
    """No existe un reporte con ese nombre."""


def reports_dir(configured: str = "") -> Path:
    return Path(configured) if configured else _REPO_REPORTS


def _path(directory: Path, name: str) -> Path:
    if not REPORT_NAME.fullmatch(name):
        raise ReportNotFound(name)
    path = directory / f"{name}.json"
    if not path.is_file():
        raise ReportNotFound(name)
    return path


@lru_cache(maxsize=32)
def _load(path: str, mtime_ns: int) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_report(directory: Path, name: str) -> dict[str, Any]:
    path = _path(directory, name)
    return _load(str(path), path.stat().st_mtime_ns)


def list_names(directory: Path) -> list[str]:
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.json") if REPORT_NAME.fullmatch(p.stem))


# --- resumen y detalle ------------------------------------------------------------------------


def summary(name: str, report: dict[str, Any]) -> dict[str, Any]:
    config = report.get("config") or {}
    approval = report.get("approval") or {}
    mc = (report.get("montecarlo") or {}).get("max_drawdown") or {}
    return {
        "name": name,
        "strategy": config.get("strategy", name),
        "symbols": config.get("symbols") or [],
        "oos_start": config.get("oos_start"),
        "oos_end": config.get("oos_end"),
        "aggregate": report.get("aggregate") or {},
        "buy_and_hold": report.get("buy_and_hold") or {},
        "max_drawdown_p95": mc.get("p95"),
        "approved": bool(approval.get("approved", False)),
        "reasons": list(approval.get("reasons") or []),
    }


def equity_curve(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Curva diaria ``{day, strategy, buy_and_hold}`` (comprar y mantener puede faltar)."""
    daily = report.get("equity_daily") or {}
    hold = {str(d)[:10]: float(v) for d, v in daily.get("buy_and_hold") or []}
    return [
        {"day": str(d)[:10], "strategy": float(v), "buy_and_hold": hold.get(str(d)[:10])}
        for d, v in daily.get("strategy") or []
    ]


def windows(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Una fila por ventana del walk-forward: in-sample, parámetros y out-of-sample."""
    out = []
    for w in report.get("windows") or []:
        oos = w.get("oos") or {}
        hold = w.get("buy_and_hold") or {}
        out.append(
            {
                "label": w.get("label"),
                "is_start": w.get("is_start"),
                "is_end": w.get("is_end"),
                "oos_start": w.get("oos_start"),
                "oos_end": w.get("oos_end"),
                "params_label": w.get("params_label"),
                "is_sharpe": w.get("is_sharpe"),
                "oos_return": oos.get("total_return"),
                "oos_sharpe": oos.get("sharpe"),
                "oos_max_drawdown": oos.get("max_drawdown"),
                "hold_return": hold.get("total_return"),
                "trades": w.get("trades"),
            }
        )
    return out


@lru_cache(maxsize=32)
def _histogram(path: str, mtime_ns: int) -> dict[str, Any] | None:
    report = _load(path, mtime_ns)
    returns = [r for _, r in returns_from_equity(lab_equity_points(report))]
    mc = report.get("montecarlo") or {}
    config = report.get("config") or {}
    block = int(mc.get("block_size") or config.get("block_size") or 20)
    if len(returns) < max(block, 2):
        return None
    n_sim = int(mc.get("n_simulations") or config.get("n_simulations") or 10_000)
    seed = int(config.get("seed", 42))
    horizon = int(mc.get("horizon_days") or ONE_YEAR_DAYS)
    result = bootstrap(
        np.asarray(returns, dtype=float), n_sim, seed=seed, block_size=block, horizon=horizon
    )
    samples = result.max_drawdowns
    counts, edges = np.histogram(samples, bins=HISTOGRAM_BINS, range=(0.0, float(samples.max())))
    reported = (mc.get("max_drawdown") or {}).get("p95")
    return {
        "bins": [
            {"start": float(a), "end": float(b), "count": int(c)}
            for a, b, c in zip(edges[:-1], edges[1:], counts, strict=True)
        ],
        "p95": float(reported) if reported is not None else float(result.max_drawdown.p95),
        "p95_recomputed": float(result.max_drawdown.p95),
        "p50": float(result.max_drawdown.p50),
        "n_simulations": n_sim,
        "block_size": block,
        "seed": seed,
        "horizon_days": horizon,
        "limit": (report.get("approval") or {}).get("criteria", {}).get("max_drawdown_p95"),
    }


def drawdown_histogram(directory: Path, name: str) -> dict[str, Any] | None:
    path = _path(directory, name)
    return _histogram(str(path), path.stat().st_mtime_ns)


def detail(directory: Path, name: str) -> dict[str, Any]:
    report = load_report(directory, name)
    approval = report.get("approval") or {}
    return {
        **summary(name, report),
        "config": report.get("config") or {},
        "per_symbol": report.get("per_symbol") or {},
        "parameter_stability": report.get("parameter_stability") or {},
        "montecarlo": report.get("montecarlo") or {},
        "checks": list(approval.get("checks") or []),
        "exposure_by_risk_level": approval.get("exposure_by_risk_level") or {},
        "windows": windows(report),
        "equity": equity_curve(report),
        "drawdown_histogram": drawdown_histogram(directory, name),
    }


__all__ = [
    "REPORT_NAME",
    "ReportNotFound",
    "detail",
    "drawdown_histogram",
    "equity_curve",
    "list_names",
    "load_report",
    "reports_dir",
    "summary",
    "windows",
]
