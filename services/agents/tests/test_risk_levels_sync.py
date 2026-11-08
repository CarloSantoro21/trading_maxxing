"""La tabla de niveles de riesgo vive en tm_agents.risk y la copian la base y el frontend.

Estas pruebas leen la migración de Supabase y el esquema del wizard para que ninguna copia
se desalinee de :data:`tm_agents.risk.RISK_TABLE` sin que falle el CI.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest
from tm_agents.risk import MIN_CAPITAL, RISK_TABLE

ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "supabase/migrations/20261109000100_profiles_risk_levels.sql"
WEB_SCHEMA = ROOT / "apps/web/src/lib/onboarding/schema.ts"


def _require(path: Path) -> str:
    if not path.exists():  # p. ej. dentro de la imagen de Docker, que solo copia services/
        pytest.skip(f"no existe {path.relative_to(ROOT)}")
    return path.read_text(encoding="utf-8")


def test_migration_max_drawdown_matches_risk_table():
    sql = _require(MIGRATION)
    body = sql.split("private.risk_level_max_drawdown", 1)[1].split("$$;", 1)[0]
    pairs = {int(lvl): Decimal(dd) for lvl, dd in re.findall(r"when (\d) then ([\d.]+)", body)}
    assert pairs == {lvl: spec.max_drawdown for lvl, spec in RISK_TABLE.items()}


def test_migration_min_capital_matches():
    sql = _require(MIGRATION)
    match = re.search(r"check \(capital >= (\d+)\)", sql)
    assert match and Decimal(match.group(1)) == MIN_CAPITAL


def test_web_schema_matches_risk_table():
    ts = _require(WEB_SCHEMA)
    rows = re.findall(
        r"(\d):\s*\{\s*name:\s*\"([^\"]+)\",\s*maxDrawdownPct:\s*([\d.]+),"
        r"\s*riskPerTradePct:\s*([\d.]+),\s*maxExposurePct:\s*([\d.]+),",
        ts,
    )
    hundred = Decimal(100)
    parsed = {
        int(lvl): (name, Decimal(dd) / hundred, Decimal(pt) / hundred, Decimal(ex) / hundred)
        for lvl, name, dd, pt, ex in rows
    }
    expected = {
        lvl: (spec.name, spec.max_drawdown, spec.risk_per_trade, spec.max_exposure)
        for lvl, spec in RISK_TABLE.items()
    }
    assert parsed == expected
    match = re.search(r"export const MIN_CAPITAL = (\d+);", ts)
    assert match and Decimal(match.group(1)) == MIN_CAPITAL
