"""Proyección de una meta (Monte Carlo #3, #38): la parte numérica, sin base ni red."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest
from tm_agents.strategy_stats import StrategyState
from tm_api.projection import (
    MAX_SIMULATIONS,
    MIN_OBSERVATIONS,
    MIN_SIMULATIONS,
    ProjectionError,
    StrategySeries,
    _align,
    fan_indices,
    lab_equity_points,
    project_goal,
    returns_from_equity,
    simulations_for,
)
from tm_shared.contracts import Horizon

TODAY = date(2026, 11, 8)

# Reporte completo mínimo: lo que el BotRunner lee para el peso y la exposición.
REPORT = {
    "config": {"strategy": "trend"},
    "aggregate": {"volatility": 0.35, "sharpe": 0.9},
    "montecarlo": {"max_drawdown": {"p95": 0.30}},
}


def _series(mean: float, n: int = 400, seed: int = 0, std: float = 0.02):
    rng = np.random.default_rng(seed)
    start = date(2024, 1, 1)
    return [(start + timedelta(days=i), float(r)) for i, r in enumerate(rng.normal(mean, std, n))]


def _strategy(name="trend", mean=0.002, state=StrategyState.ACTIVE, report=REPORT, **kw):
    return StrategySeries(
        name=name,
        state=state,
        lab_report=report,
        returns=kw.pop("returns", None) or _series(mean, **kw),
        source="reference",
    )


def _project(strategies, *, start=10_000.0, target=11_000.0, days=365, risk=3):
    return project_goal(
        start_equity=start,
        target_equity=target,
        risk_level=risk,
        horizon=Horizon.MEDIUM,
        today=TODAY,
        deadline=TODAY + timedelta(days=days),
        strategies=strategies,
    )


def test_returns_from_equity_ordena_y_omite_invalidos():
    d = date(2025, 1, 1)
    points = [(d + timedelta(days=2), 121.0), (d, 100.0), (d + timedelta(days=1), 110.0)]
    points.append((d + timedelta(days=3), float("nan")))
    out = returns_from_equity(points)
    assert [x[0] for x in out] == [d + timedelta(days=1), d + timedelta(days=2)]
    assert out[0][1] == pytest.approx(0.1)
    assert out[1][1] == pytest.approx(0.1)
    assert returns_from_equity([]) == []


def test_lab_equity_points_lee_equity_daily_y_tolera_basura():
    report = {
        "equity_daily": {
            "strategy": [["2025-01-01", 1.0], ["mal", 2], [None], ["2025-01-02T00:00:00", "3"]]
        }
    }
    assert lab_equity_points(report) == [(date(2025, 1, 1), 1.0), (date(2025, 1, 2), 3.0)]
    assert lab_equity_points(None) == []
    assert lab_equity_points({"equity_daily": {}}) == []


def test_fan_indices_y_simulaciones():
    assert fan_indices(5) == [0, 1, 2, 3, 4, 5]
    idx = fan_indices(1000)
    assert idx[0] == 0 and idx[-1] == 1000 and len(idx) == 60
    assert simulations_for(30) == MAX_SIMULATIONS
    assert simulations_for(3653) < MAX_SIMULATIONS
    assert simulations_for(10**9) == MIN_SIMULATIONS


def test_estrategia_rentable_da_probabilidad_alta_y_abanico_ordenado():
    result = _project([_strategy(mean=0.003)])
    assert 0.5 < result.probability <= 1.0
    # Tocar la meta en algún día es al menos tan probable como estar arriba al final.
    assert result.probability >= result.probability_at_deadline
    assert result.days == 365
    assert result.fan[0].day == TODAY and result.fan[-1].day == TODAY + timedelta(days=365)
    assert result.fan[0].p50 == pytest.approx(10_000.0)
    for p in result.fan:
        assert p.p10 <= p.p50 <= p.p90
    assert result.final_equity_p10 <= result.final_equity_p50 <= result.final_equity_p90
    (c,) = result.strategies
    # Un solo sleeve activo: peso 1; exposición del nivel 3 (20 %) contra un p95 de 30 %.
    assert c.weight == pytest.approx(1.0)
    assert 0 < c.exposure < 1
    assert result.invested_fraction == pytest.approx(c.exposure)
    assert result.n_simulations == MAX_SIMULATIONS and result.block_size == 20


def test_es_determinista():
    a = _project([_strategy(mean=0.001)])
    b = _project([_strategy(mean=0.001)])
    assert a.probability == b.probability
    assert a.fan == b.fan


def test_estrategia_perdedora_da_probabilidad_baja():
    result = _project([_strategy(mean=-0.003)], target=12_000.0)
    assert result.probability < 0.1


def test_mas_riesgo_mas_exposicion():
    low = _project([_strategy()], risk=1)
    high = _project([_strategy()], risk=5)
    assert low.strategies[0].exposure < high.strategies[0].exposure
    assert high.strategies[0].exposure == pytest.approx(1.0)


def test_sin_estrategias_el_equity_no_cambia():
    result = _project([])
    assert result.probability == 0.0
    assert result.invested_fraction == 0.0
    assert result.n_simulations == 0
    assert all(p.p10 == p.p50 == p.p90 == 10_000.0 for p in result.fan)
    assert result.notes


def test_meta_ya_alcanzada_sin_estrategias_es_1():
    result = _project([], start=12_000.0, target=11_000.0)
    assert result.probability == 1.0 and result.probability_at_deadline == 1.0


def test_historial_corto_y_sin_p95_quedan_fuera():
    short = _strategy(name="corta", returns=_series(0.002, n=MIN_OBSERVATIONS - 1))
    no_p95 = _strategy(name="sin_p95", report={"aggregate": {"volatility": 0.3, "sharpe": 1}})
    result = _project([short, no_p95])
    by_name = {c.name: c for c in result.strategies}
    assert "historial suficiente" in by_name["corta"].excluded
    assert "p95" in by_name["sin_p95"].excluded
    assert result.invested_fraction == 0.0


def test_shadow_no_recibe_capital():
    result = _project([_strategy(state=StrategyState.SHADOW)])
    assert result.strategies[0].excluded is not None
    assert result.probability == 0.0


def test_reporte_plano_usa_la_serie_para_volatilidad_y_sharpe():
    flat = {"sharpe_oos": 0.8, "max_drawdown_p95": 0.3}
    result = _project([_strategy(report=flat)])
    c = result.strategies[0]
    assert c.excluded is None and c.weight == pytest.approx(1.0)
    # Sin sharpe_oos, el Sharpe sale de la serie (positivo con media positiva).
    result = _project([_strategy(report={"max_drawdown_p95": 0.3}, mean=0.003)])
    assert result.strategies[0].excluded is None


def test_dos_estrategias_y_nota_del_laboratorio():
    a = _strategy(name="trend", seed=1)
    b = StrategySeries(
        name="trend_vol",
        state=StrategyState.PROBATION,
        lab_report={**REPORT, "config": {"strategy": "trend_vol"}},
        returns=_series(0.002, seed=2),
        source="lab_report",
    )
    result = _project([a, b])
    weights = {c.name: c.weight for c in result.strategies}
    assert weights["trend"] > weights["trend_vol"] > 0
    assert any("laboratorio" in n for n in result.notes)


def test_alinea_por_posicion_si_las_fechas_no_coinciden():
    a = _series(0.001, n=100)
    b = [(d + timedelta(days=1000), r) for d, r in _series(0.001, n=80, seed=3)]
    aligned = _align({"a": a, "b": b})
    assert aligned["a"].size == aligned["b"].size == 80
    assert aligned["a"][-1] == pytest.approx(a[-1][1])
    assert _align({}) == {}


@pytest.mark.parametrize(
    ("days", "start", "message"),
    [
        (0, 10_000.0, "ya pasó"),
        (-3, 10_000.0, "ya pasó"),
        (4000, 10_000.0, "10 años"),
        (10, 0.0, "mayor que 0"),
    ],
)
def test_errores(days, start, message):
    with pytest.raises(ProjectionError, match=message):
        _project([_strategy()], days=days, start=start)
