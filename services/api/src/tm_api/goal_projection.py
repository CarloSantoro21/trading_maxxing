"""Lectura de lo que necesita la proyección de una meta (#38), con la sesión del usuario.

- **Perfil**: capital (base del retorno objetivo), nivel de riesgo y horizonte.
- **Equity actual**: la última foto de ``equity_snapshots`` de cada bot del perfil; sin
  fotos, el capital (en spot, sin posición = todo en USDT). Es la misma regla de
  ``GET /portfolio``.
- **Estrategias que reciben capital** (``active`` y ``probation``) con su ``lab_report``. Si
  el reporte de la base está resumido (sin ``aggregate`` ni curva diaria, como el del seed),
  se usa el archivo del laboratorio con el mismo nombre (``LAB_REPORTS_DIR``).
- **Retornos diarios**: los del portafolio de referencia (``reference_equity``, la corrida
  actual) si tiene al menos :data:`~tm_api.projection.MIN_REFERENCE_DAYS` días; si no, la
  curva out-of-sample del reporte.

Las tablas ``strategies`` y ``reference_equity`` las lee cualquier usuario autenticado (RLS).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from tm_agents.strategy_stats import StrategyState
from tm_shared.contracts import Horizon

from tm_api.auth import CurrentUser
from tm_api.db import Database, Row
from tm_api.deps import not_found
from tm_api.lab_reports import REPORT_NAME, ReportNotFound, load_report, reports_dir
from tm_api.projection import (
    MIN_REFERENCE_DAYS,
    StrategySeries,
    lab_equity_points,
    returns_from_equity,
)

#: Estados con capital (los que el PortfolioAllocator no excluye por estado).
CAPITAL_STATES = (StrategyState.ACTIVE, StrategyState.PROBATION)


@dataclass(frozen=True)
class ProjectionInputs:
    profile_id: str
    capital: float
    start_equity: float
    target_equity: float
    risk_level: int
    horizon: Horizon
    today: date
    deadline: date
    strategies: list[StrategySeries]


def _num(value: object) -> float:
    return float(value) if value is not None else 0.0  # type: ignore[arg-type]


def _in(ids: list[str]) -> str:
    return f"in.({','.join(ids)})"


async def current_equity(db: Database, token: str, profile: Row) -> float:
    capital = _num(profile["capital"])
    bots = await db.select("bots", token, {"profile_id": f"eq.{profile['id']}", "select": "id"})
    total, found = 0.0, False
    for bot in bots:
        rows = await db.select(
            "equity_snapshots",
            token,
            {"bot_id": f"eq.{bot['id']}", "order": "ts.desc", "limit": "1"},
        )
        if rows:
            total += _num(rows[0]["equity"])
            found = True
    return total if found else capital


def _has_series(report: dict[str, Any] | None) -> bool:
    return bool(report and report.get("aggregate") and lab_equity_points(report))


def effective_report(row: Row, lab_dir: str) -> tuple[dict[str, Any] | None, str]:
    """``(reporte, origen)``: el de la base si está completo; si no, el archivo del lab."""
    report = row.get("lab_report")
    if _has_series(report):
        return report, "db"
    lab_name = str(((report or {}).get("config") or {}).get("strategy") or row["name"])
    if REPORT_NAME.fullmatch(lab_name):
        try:
            return load_report(reports_dir(lab_dir), lab_name), "file"
        except (ReportNotFound, ValueError):
            pass
    return report, "db" if report else "none"


def _reference_points(rows: list[Row]) -> dict[str, list[tuple[date, float]]]:
    """Curva de la corrida actual (último ``started_at``) de cada estrategia."""
    latest: dict[str, str] = {}
    for r in rows:
        sid = str(r["strategy_id"])
        latest[sid] = max(latest.get(sid, ""), str(r["started_at"]))
    out: dict[str, list[tuple[date, float]]] = defaultdict(list)
    for r in rows:
        sid = str(r["strategy_id"])
        if str(r["started_at"]) == latest[sid]:
            out[sid].append((date.fromisoformat(str(r["day"])[:10]), _num(r["equity"])))
    return out


async def load_projection_inputs(
    db: Database, user: CurrentUser, goal: Row, lab_dir: str
) -> ProjectionInputs:
    profiles = await db.select(
        "profiles", user.token, {"id": f"eq.{goal['profile_id']}", "limit": "1"}
    )
    if not profiles:
        raise not_found("Perfil")
    profile = profiles[0]
    capital = _num(profile["capital"])
    start_equity = await current_equity(db, user.token, profile)

    states = ",".join(s.value for s in CAPITAL_STATES)
    strategies = await db.select(
        "strategies", user.token, {"state": f"in.({states})", "order": "name.asc"}
    )
    reference: dict[str, list[tuple[date, float]]] = {}
    if strategies:
        rows = await db.select(
            "reference_equity",
            user.token,
            {
                "strategy_id": _in([str(s["id"]) for s in strategies]),
                "select": "strategy_id,started_at,day,equity",
                "order": "day.asc",
            },
        )
        reference = _reference_points(rows)

    series: list[StrategySeries] = []
    for row in strategies:
        report, report_source = effective_report(row, lab_dir)
        ref_returns = returns_from_equity(reference.get(str(row["id"]), []))
        if len(ref_returns) >= MIN_REFERENCE_DAYS:
            returns, source = ref_returns, "reference"
        else:
            returns = returns_from_equity(lab_equity_points(report))
            source = "lab_report" if returns else "none"
        series.append(
            StrategySeries(
                name=str(row["name"]),
                state=StrategyState(row["state"]),
                lab_report=report,
                returns=returns,
                source=source,
                report_source=report_source,
            )
        )

    return ProjectionInputs(
        profile_id=str(profile["id"]),
        capital=capital,
        start_equity=start_equity,
        target_equity=capital * (1.0 + _num(goal["target_return"])),
        risk_level=int(profile["risk_level"]),
        horizon=Horizon(profile.get("horizon") or "medium"),
        today=datetime.now(UTC).date(),
        deadline=date.fromisoformat(str(goal["deadline"])[:10]),
        strategies=series,
    )
