"""Pruebas del MonteCarloService contra casos con solución analítica o enumerable."""

import itertools
import math
import time

import numpy as np
import pandas as pd
import pytest
from tm_agents.montecarlo import (
    ApprovalCriteria,
    MCReport,
    bootstrap,
    evaluate,
    reshuffle,
    simulate,
)
from tm_agents.montecarlo.report import build_report
from tm_agents.montecarlo.resampling import bootstrap_paths


def reference_max_drawdown(returns, initial_capital=1.0):
    """Drawdown máximo de un solo camino, calculado con un loop simple (referencia)."""
    equity = peak = initial_capital
    worst = 0.0
    for r in returns:
        equity *= 1.0 + r
        peak = max(peak, equity)
        worst = max(worst, 1.0 - equity / peak)
    return worst


def _report_from_paths(paths, **kwargs):
    params = {
        "method": "reshuffle",
        "initial_capital": 1.0,
        "ruin_level": 0.5,
        "block_size": 1,
        "trades_per_year": None,
    }
    params.update(kwargs)
    return build_report(np.array(paths, dtype=np.float64), **params)


# --- Casos analíticos --------------------------------------------------------


@pytest.mark.parametrize("method", ["reshuffle", "bootstrap"])
def test_identical_losing_trades_have_zero_variance_and_known_drawdown(method):
    trades = np.full(50, -0.01)
    report = simulate(trades, 500, seed=1, method=method, initial_capital=1_000)

    expected_dd = 1.0 - 0.99**50
    assert report.max_drawdown.std == 0.0
    assert report.final_return.std == 0.0
    for value in (report.max_drawdown.p5, report.max_drawdown.p50, report.max_drawdown.p99):
        assert value == pytest.approx(expected_dd, rel=1e-12)
    assert report.final_return.p50 == pytest.approx(0.99**50 - 1.0, rel=1e-12)
    assert report.per_trade_return.p50 == pytest.approx(-0.01, rel=1e-12)
    np.testing.assert_allclose(report.equity_fan.p10, report.equity_fan.p90)
    np.testing.assert_allclose(report.equity_fan.p50, 1_000 * 0.99 ** np.arange(51))


def test_identical_winning_trades_have_zero_drawdown():
    report = reshuffle(np.full(100, 0.02), 200, seed=3)
    assert report.max_drawdown.max == 0.0
    assert report.final_return.p50 == pytest.approx(1.02**100 - 1.0)
    assert report.probability_of_ruin == 0.0


def test_monotone_increasing_sequences_have_zero_drawdown():
    # Cualquier permutación de retornos no negativos da un equity no decreciente
    trades = np.linspace(0.0, 0.05, 40)
    for method in ("reshuffle", "bootstrap"):
        report = simulate(trades, 1_000, seed=5, method=method)
        assert np.all(report.max_drawdowns == 0.0)


def test_reshuffle_preserves_final_return_exactly_with_exact_floats():
    # Factores 2, 0.5, 1.5 y 0.75: todos los productos son exactos en float64,
    # así que el orden no introduce redondeo.
    trades = np.array([1.0, -0.5, 0.5, -0.25] * 5)
    report = reshuffle(trades, 2_000, seed=11)
    expected = np.prod(1.0 + trades) - 1.0
    assert np.all(report.final_returns == expected)
    assert report.final_return.std == 0.0


def test_reshuffle_preserves_final_return_up_to_rounding():
    trades = np.random.default_rng(0).normal(0.001, 0.02, 500)
    report = reshuffle(trades, 2_000, seed=7)
    expected = np.prod(1.0 + trades) - 1.0
    np.testing.assert_allclose(report.final_returns, expected, rtol=1e-11)
    # El camino sí cambia: el drawdown no es constante
    assert report.max_drawdown.std > 0.0


def test_max_drawdown_matches_reference_on_known_path():
    # Equity: 1 -> 1.1 -> 0.88 -> 1.144: pico 1.1, valle 0.88 => DD = 20 %
    report = _report_from_paths([[0.1, -0.2, 0.3]])
    assert report.max_drawdowns[0] == pytest.approx(0.2)


def test_drawdown_counts_from_initial_capital():
    report = _report_from_paths([[-0.1, 0.5]])
    assert report.max_drawdowns[0] == pytest.approx(0.1)


# --- Caso enumerable ---------------------------------------------------------

# 4 trades, 24 permutaciones; DD exactos: 28 % (12), 20 % (8) y 20.8 % (4)
SMALL_TRADES = (0.1, -0.2, -0.1, 0.25)
ALL_PERMUTATIONS = list(itertools.permutations(SMALL_TRADES))
EXACT_DD = {perm: reference_max_drawdown(perm) for perm in ALL_PERMUTATIONS}


def test_enumerated_permutations_match_reference():
    report = _report_from_paths(ALL_PERMUTATIONS)
    expected = [EXACT_DD[p] for p in ALL_PERMUTATIONS]
    np.testing.assert_allclose(report.max_drawdowns, expected, rtol=1e-12)
    assert sorted(np.round(expected, 12)) == [0.2] * 8 + [0.208] * 4 + [0.28] * 12
    # Mediana exacta de las 24 permutaciones
    assert report.max_drawdown.p50 == pytest.approx(np.median(expected))


def test_reshuffle_frequencies_match_uniform_permutations():
    n = 60_000
    report = reshuffle(SMALL_TRADES, n, seed=2024)
    exact_values = np.array(sorted(EXACT_DD.values()))

    # Cada DD observado corresponde a alguna permutación
    dist = np.abs(report.max_drawdowns[:, None] - exact_values[None, :]).min(axis=1)
    assert dist.max() < 1e-12

    # Media del DD ≈ promedio exacto sobre las 24 permutaciones (4 sigmas)
    exact_mean = exact_values.mean()
    sigma = exact_values.std() / math.sqrt(n)
    assert abs(report.max_drawdown.mean - exact_mean) < 4 * sigma

    # Frecuencia de cada valor distinto ≈ (número de permutaciones con ese valor) / 24
    for value in np.unique(np.round(exact_values, 12)):
        p = np.isclose(exact_values, value).mean()
        freq = np.isclose(report.max_drawdowns, value).mean()
        assert abs(freq - p) < 4 * math.sqrt(p * (1 - p) / n)


def test_probability_of_ruin_checks_the_whole_path_not_only_the_end():
    # [-50 %, +100 %] y [+100 %, -50 %] terminan ambos en el capital inicial,
    # pero solo el primero toca 0.5. Con umbral 0.6, P(ruina) = 1/2 exacta.
    n = 40_000
    report = reshuffle([-0.5, 1.0], n, seed=9, ruin_level=0.6)
    assert np.all(report.final_returns == 0.0)
    assert abs(report.probability_of_ruin - 0.5) < 4 * math.sqrt(0.25 / n)

    # Si el umbral está por debajo del valle (0.5), nunca hay ruina
    assert reshuffle([-0.5, 1.0], 1_000, seed=9, ruin_level=0.4).probability_of_ruin == 0.0


def test_total_loss_trade_is_handled():
    report = reshuffle([0.1, -1.0, 0.2], 100, seed=0)
    assert np.all(report.max_drawdowns == 1.0)
    assert report.probability_of_ruin == 1.0
    assert report.per_trade_return.p50 == -1.0


# --- Bootstrap ---------------------------------------------------------------


@pytest.mark.parametrize("block_size", [1, 7])
def test_bootstrap_mean_matches_theoretical_mean(block_size):
    trades = np.random.default_rng(1).normal(0.003, 0.02, 200)
    n = 5_000
    rng = np.random.default_rng(123)
    paths = bootstrap_paths(trades, n, rng, block_size)
    # Cada posición se muestrea de forma uniforme: E[media] = media de los trades
    sigma = trades.std() / math.sqrt(n * trades.size)
    # Con bloques las muestras están correlacionadas: se amplía la tolerancia
    tolerance = 4 * sigma * math.sqrt(block_size)
    assert abs(paths.mean() - trades.mean()) < tolerance


def test_bootstrap_expected_final_equity_iid():
    # Bootstrap iid: E[prod(1 + r)] = (1 + media)^m
    trades = np.array([0.05, -0.03, 0.02, -0.01])
    m = trades.size
    report = bootstrap(trades, 50_000, seed=8)
    growth = 1.0 + report.final_returns
    expected = (1.0 + trades.mean()) ** m
    assert abs(growth.mean() - expected) < 4 * growth.std() / math.sqrt(growth.size)


def test_block_bootstrap_keeps_contiguous_blocks():
    m, block = 50, 5
    returns = np.arange(m, dtype=np.float64)
    paths = bootstrap_paths(returns, 300, np.random.default_rng(4), block)
    assert paths.shape == (300, m)
    blocks = paths.reshape(300, m // block, block)
    steps = (np.diff(blocks, axis=2) % m).astype(int)
    assert np.all(steps == 1)


def test_block_bootstrap_truncates_last_block():
    paths = bootstrap_paths(np.arange(10, dtype=np.float64), 20, np.random.default_rng(0), 4)
    assert paths.shape == (20, 10)


def test_bootstrap_does_not_preserve_final_return():
    trades = np.random.default_rng(2).normal(0.0, 0.02, 100)
    report = bootstrap(trades, 1_000, seed=3, block_size=5)
    assert report.final_return.std > 0.0
    assert report.method == "bootstrap"
    assert report.block_size == 5


# --- Reporte -----------------------------------------------------------------


def test_report_shapes_and_percentile_order():
    trades = np.random.default_rng(3).normal(0.002, 0.015, 120)
    report = bootstrap(trades, 2_000, seed=1, initial_capital=10_000, trades_per_year=250)
    assert isinstance(report, MCReport)
    assert report.n_simulations == 2_000
    assert report.n_trades == 120
    assert report.max_drawdowns.shape == (2_000,)
    fan = report.equity_fan
    assert fan.p10.shape == fan.p50.shape == fan.p90.shape == (121,)
    assert fan.p50[0] == 10_000
    assert np.all(fan.p10 <= fan.p50) and np.all(fan.p50 <= fan.p90)
    dd = report.max_drawdown
    assert 0.0 <= dd.min <= dd.p5 <= dd.p50 <= dd.p95 <= dd.p99 <= dd.max <= 1.0
    assert report.cagr is not None
    assert report.cagr.p50 == pytest.approx((1 + report.per_trade_return.p50) ** 250 - 1, rel=1e-9)


def test_cagr_is_none_without_trades_per_year():
    assert reshuffle([0.01, -0.01], 10, seed=0).cagr is None


# --- Reproducibilidad --------------------------------------------------------


@pytest.mark.parametrize("method", ["reshuffle", "bootstrap"])
def test_same_seed_gives_identical_results(method):
    trades = np.random.default_rng(0).normal(0.001, 0.02, 80)
    a = simulate(trades, 500, seed=42, method=method)
    b = simulate(trades, 500, seed=42, method=method)
    np.testing.assert_array_equal(a.max_drawdowns, b.max_drawdowns)
    np.testing.assert_array_equal(a.final_returns, b.final_returns)
    np.testing.assert_array_equal(a.equity_fan.p50, b.equity_fan.p50)
    c = simulate(trades, 500, seed=43, method=method)
    assert not np.array_equal(a.max_drawdowns, c.max_drawdowns)


def test_generator_seed_is_accepted():
    trades = [0.01, -0.02, 0.03, -0.01]
    a = reshuffle(trades, 100, seed=np.random.default_rng(5))
    b = reshuffle(trades, 100, seed=5)
    np.testing.assert_array_equal(a.max_drawdowns, b.max_drawdowns)


def test_accepts_lists_and_pandas_series():
    trades = [0.01, -0.02, 0.015]
    a = reshuffle(trades, 50, seed=1)
    b = reshuffle(pd.Series(trades), 50, seed=1)
    np.testing.assert_array_equal(a.max_drawdowns, b.max_drawdowns)


# --- Validación de entradas --------------------------------------------------


@pytest.mark.parametrize(
    ("trades", "error"),
    [
        ([], ValueError),
        (np.array([]), ValueError),
        ([[0.01, 0.02]], ValueError),
        (0.01, ValueError),
        ([0.01, np.nan], ValueError),
        ([0.01, np.inf], ValueError),
        ([0.01, -1.5], ValueError),
        ("0.01", TypeError),
        (["a", "b"], TypeError),
    ],
)
def test_invalid_trades_are_rejected(trades, error):
    with pytest.raises(error):
        reshuffle(trades, 10, seed=0)


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"n_simulations": 0}, ValueError),
        ({"n_simulations": 1.5}, TypeError),
        ({"n_simulations": True}, TypeError),
        ({"seed": None}, TypeError),
        ({"seed": 1.0}, TypeError),
        ({"method": "jackknife"}, ValueError),
        ({"initial_capital": 0}, ValueError),
        ({"initial_capital": float("nan")}, ValueError),
        ({"ruin_level": 1.0}, ValueError),
        ({"ruin_level": -0.1}, ValueError),
        ({"block_size": 0}, ValueError),
        ({"block_size": 2}, ValueError),  # reshuffle no usa bloques
        ({"method": "bootstrap", "block_size": 11}, ValueError),
        ({"trades_per_year": 0}, ValueError),
        ({"trades_per_year": float("inf")}, ValueError),
    ],
)
def test_invalid_parameters_are_rejected(kwargs, error):
    params = {"n_simulations": 10, "seed": 0}
    params.update(kwargs)
    with pytest.raises(error):
        simulate(np.full(10, 0.01), **params)


def test_seed_is_required():
    with pytest.raises(TypeError):
        reshuffle([0.01, 0.02], 10)  # type: ignore[call-arg]


# --- Criterios de aprobación -------------------------------------------------


def _report_with_dd(dd_per_trade=-0.01, n=100):
    # Trades idénticos: el DD p95 es exactamente 1 - (1 + r)^n
    return reshuffle(np.full(n, dd_per_trade), 10, seed=0)


def test_evaluate_approves_when_all_criteria_pass():
    report = reshuffle(np.full(150, 0.01), 100, seed=0)
    result = evaluate(report, oos_sharpe=0.9, n_trades=150)
    assert result.approved
    assert result.reasons == ()
    assert [c.name for c in result.checks] == ["oos_sharpe", "max_drawdown_p95", "min_trades"]


def test_evaluate_reports_every_failing_criterion():
    report = _report_with_dd(-0.01, 50)  # DD = 1 - 0.99^50 ≈ 39.5 %
    result = evaluate(report, oos_sharpe=0.5, n_trades=50)
    assert not result.approved
    assert len(result.reasons) == 3
    joined = " | ".join(result.reasons)
    assert "Sharpe out-of-sample de 0.50" in joined
    assert "39.5%" in joined
    assert "50 trades (se requieren al menos 100)" in joined


def test_evaluate_uses_custom_criteria_and_strict_thresholds():
    report = _report_with_dd(-0.001, 100)  # DD ≈ 9.5 %
    lenient = ApprovalCriteria(min_oos_sharpe=0.2, max_drawdown_p95=0.10, min_trades=100)
    assert evaluate(report, oos_sharpe=0.3, n_trades=100, criteria=lenient).approved

    strict = ApprovalCriteria(max_drawdown_p95=0.05)
    result = evaluate(report, oos_sharpe=1.0, n_trades=100, criteria=strict)
    assert not result.approved
    assert [c.name for c in result.checks if not c.passed] == ["max_drawdown_p95"]


def test_evaluate_rejects_nan_sharpe():
    report = reshuffle(np.full(150, 0.01), 10, seed=0)
    result = evaluate(report, oos_sharpe=float("nan"), n_trades=150)
    assert not result.approved
    assert result.checks[0].name == "oos_sharpe" and not result.checks[0].passed


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_oos_sharpe": float("nan")},
        {"max_drawdown_p95": 0.0},
        {"max_drawdown_p95": 1.5},
        {"min_trades": 0},
    ],
)
def test_invalid_criteria_are_rejected(kwargs):
    with pytest.raises(ValueError):
        ApprovalCriteria(**kwargs)


# --- Rendimiento -------------------------------------------------------------


@pytest.mark.parametrize("method", ["reshuffle", "bootstrap"])
def test_performance_10k_simulations_of_500_trades(method):
    # Objetivo: < 2 s en CPU. En CI se deja margen (runners compartidos).
    trades = np.random.default_rng(0).normal(0.002, 0.02, 500)
    simulate(trades, 100, seed=0, method=method)  # calentamiento
    start = time.perf_counter()
    report = simulate(trades, 10_000, seed=0, method=method, block_size=1)
    elapsed = time.perf_counter() - start
    assert report.n_simulations == 10_000
    assert elapsed < 4.0, f"{method}: {elapsed:.2f} s para 10,000 x 500"


# --- Alternativa de baja frecuencia (docs/estrategias/criterios-de-aprobacion.md) ---


@pytest.mark.parametrize(
    "n_trades,oos_years,expected",
    [
        (40, 4.0, True),  # trend following: pocos trades, años suficientes
        (30, 3.0, True),  # justo en los umbrales
        (29, 4.0, False),  # muy pocos trades
        (40, 2.5, False),  # pocos años
        (40, None, False),  # sin años no aplica la alternativa
        (120, None, True),  # la regla normal sigue funcionando
    ],
)
def test_low_frequency_alternative(n_trades, oos_years, expected):
    report = reshuffle(np.full(150, 0.01), 100, seed=0)
    result = evaluate(report, oos_sharpe=0.9, n_trades=n_trades, oos_years=oos_years)
    check = next(c for c in result.checks if c.name == "min_trades")
    assert check.passed is expected
    assert "o 30 con 3 años out-of-sample" in check.reason


@pytest.mark.parametrize(
    "kwargs",
    [{"low_freq_min_trades": 0}, {"low_freq_min_trades": 200}, {"low_freq_min_oos_years": 0}],
)
def test_low_frequency_criteria_validation(kwargs):
    with pytest.raises(ValueError):
        ApprovalCriteria(**kwargs)
