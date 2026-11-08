"""Pruebas del PortfolioAllocator, de StrategyStats y de ApprovalCriteria.for_profile."""

from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from tm_agents.allocator import DEFAULT_HORIZON_WEIGHTS, PortfolioAllocator
from tm_agents.montecarlo import ApprovalCriteria
from tm_agents.strategy_stats import StrategyState, StrategyStats
from tm_shared.contracts import Horizon

ACTIVE = StrategyState.ACTIVE
PROBATION = StrategyState.PROBATION


def stats(name, state=ACTIVE, vol=0.2, sharpe=1.0, **kw):
    return StrategyStats(name, state, volatility=vol, oos_sharpe=sharpe, **kw)


def total(sleeves):
    return sum(sleeves.values(), Decimal("0"))


@pytest.fixture
def alloc():
    return PortfolioAllocator()


# --- suma y proporcionalidad -------------------------------------------------------------
@pytest.mark.parametrize("capital", ["1000", "1234.57", "999.99", "50000", "10000.005"])
def test_sum_equals_capital(alloc, capital):
    strategies = [stats("a", vol=0.13), stats("b", vol=0.29), stats("c", vol=0.41)]
    res = alloc.allocate_detailed(Decimal(capital), Horizon.MEDIUM, strategies)
    q = Decimal("0.01")
    assert total(res.sleeves) == Decimal(capital).quantize(q, rounding="ROUND_DOWN")
    assert total(res.sleeves) + res.unallocated == Decimal(capital)
    assert all(v == v.quantize(q) for v in res.sleeves.values())


def test_inverse_volatility(alloc):
    out = alloc.allocate(Decimal("900"), Horizon.MEDIUM, [stats("a", vol=0.1), stats("b", vol=0.2)])
    assert out == {"a": Decimal("600.00"), "b": Decimal("300.00")}


def test_result_is_deterministic_and_keeps_order(alloc):
    strategies = [stats("z", vol=0.3), stats("a", vol=0.1)]
    first = alloc.allocate(Decimal("1000"), Horizon.MEDIUM, strategies)
    assert list(first) == ["z", "a"]
    assert first == alloc.allocate(Decimal("1000"), Horizon.MEDIUM, strategies)


# --- estados del ciclo de vida -----------------------------------------------------------
@pytest.mark.parametrize(
    "state",
    [StrategyState.RESEARCH, StrategyState.LAB, StrategyState.SHADOW, StrategyState.RETIRED],
)
def test_states_without_capital_get_zero(alloc, state):
    res = alloc.allocate_detailed(
        Decimal("1000"), Horizon.MEDIUM, [stats("a"), stats("x", state=state)]
    )
    assert res.sleeves == {"a": Decimal("1000.00"), "x": Decimal("0")}
    assert state.value in res.excluded["x"]


def test_probation_gets_half_and_frees_to_active(alloc):
    # Con el mismo peso base, observación recibe la mitad de lo que tendría (500 → 250)
    res = alloc.allocate_detailed(
        Decimal("1000"), Horizon.MEDIUM, [stats("a"), stats("p", state=PROBATION)]
    )
    assert res.sleeves == {"a": Decimal("750.00"), "p": Decimal("250.00")}
    assert res.unallocated == 0


def test_probation_alone_keeps_half_unallocated(alloc):
    res = alloc.allocate_detailed(Decimal("1000"), Horizon.MEDIUM, [stats("p", state=PROBATION)])
    assert res.sleeves == {"p": Decimal("500.00")}
    assert res.unallocated == Decimal("500")


def test_state_accepts_string_value():
    s = StrategyStats("a", "probation", volatility=0.1, oos_sharpe=1.0)
    assert s.state is PROBATION
    assert PROBATION.gets_capital and not StrategyState.SHADOW.gets_capital


# --- correlación -------------------------------------------------------------------------
def test_correlation_matrix_penalizes_correlated_pair(alloc):
    strategies = [stats("a"), stats("b"), stats("c")]
    no_corr = alloc.allocate(Decimal("900"), Horizon.MEDIUM, strategies)
    matrix = pd.DataFrame(
        [[1.0, 0.9, 0.0], [0.9, 1.0, 0.0], [0.0, 0.0, 1.0]],
        index=["a", "b", "c"],
        columns=["a", "b", "c"],
    )
    with_corr = alloc.allocate(Decimal("900"), Horizon.MEDIUM, strategies, correlation=matrix)
    assert no_corr == {"a": Decimal("300.00"), "b": Decimal("300.00"), "c": Decimal("300.00")}
    assert with_corr["c"] > no_corr["c"]
    assert with_corr["a"] == with_corr["b"] < no_corr["a"]
    assert total(with_corr) == Decimal("900")


def test_negative_correlation_is_not_rewarded(alloc):
    strategies = [stats("a"), stats("b")]
    out = alloc.allocate(Decimal("100"), Horizon.MEDIUM, strategies, correlation={"a": {"b": -0.8}})
    assert out == {"a": Decimal("50.00"), "b": Decimal("50.00")}


def test_correlation_estimated_from_returns(alloc):
    rng = np.random.default_rng(0)
    base = rng.normal(0, 0.01, 200)
    strategies = [
        stats("a", returns=base),
        stats("b", returns=base + rng.normal(0, 0.001, 200)),
        stats("c", returns=rng.normal(0, 0.01, 200)),
    ]
    out = alloc.allocate(Decimal("900"), Horizon.MEDIUM, strategies)
    assert out["c"] > out["a"] and out["c"] > out["b"]


def test_mapping_takes_precedence_over_returns_and_missing_pairs_fall_back(alloc):
    r = [0.01, -0.02, 0.03, 0.0, 0.01]
    strategies = [stats("a", returns=r), stats("b", returns=r), stats("c")]
    # (a, b) explícito en 0; c no tiene retornos, así que su rho es 0
    out = alloc.allocate(Decimal("900"), Horizon.MEDIUM, strategies, correlation={"b": {"a": 0.0}})
    assert out == {"a": Decimal("300.00"), "b": Decimal("300.00"), "c": Decimal("300.00")}
    # Sin la matriz, a y b se estiman perfectamente correlacionadas
    out = alloc.allocate(Decimal("900"), Horizon.MEDIUM, strategies)
    assert out["c"] > out["a"]


@pytest.mark.parametrize(
    "ra,rb", [([0.01, 0.02], [0.01, 0.02]), ([0.0, 0.0, 0.0], [0.01, 0.02, 0.03])]
)
def test_returns_too_short_or_constant_give_zero_correlation(alloc, ra, rb):
    strategies = [stats("a", returns=ra), stats("b", returns=rb), stats("c")]
    out = alloc.allocate(Decimal("900"), Horizon.MEDIUM, strategies)
    assert out == {"a": Decimal("300.00"), "b": Decimal("300.00"), "c": Decimal("300.00")}


def test_zero_penalty_disables_correlation():
    alloc = PortfolioAllocator(correlation_penalty=0)
    out = alloc.allocate(
        Decimal("900"),
        Horizon.MEDIUM,
        [stats("a"), stats("b"), stats("c")],
        correlation={"a": {"b": 1.0}},
    )
    assert out == {"a": Decimal("300.00"), "b": Decimal("300.00"), "c": Decimal("300.00")}


# --- horizonte ---------------------------------------------------------------------------
def test_horizon_weights_favor_matching_strategies(alloc):
    strategies = [stats("intraday", horizon=Horizon.SHORT), stats("trend", horizon=Horizon.LONG)]
    short = alloc.allocate(Decimal("1000"), Horizon.SHORT, strategies)
    long_ = alloc.allocate(Decimal("1000"), Horizon.LONG, strategies)
    assert short["intraday"] > short["trend"]
    assert long_["trend"] > long_["intraday"]
    # 1.0 contra 0.5 -> 2/3 y 1/3
    assert short == {"intraday": Decimal("666.67"), "trend": Decimal("333.33")}


def test_custom_horizon_weights_zero_excludes():
    weights = {**DEFAULT_HORIZON_WEIGHTS, Horizon.SHORT: {Horizon.LONG: 0.0}}
    alloc = PortfolioAllocator(horizon_weights=weights)
    res = alloc.allocate_detailed(
        Decimal("1000"),
        Horizon.SHORT,
        [stats("a", horizon=Horizon.SHORT), stats("b", horizon=Horizon.LONG)],
    )
    # SHORT no está en la tabla nueva del perfil corto: cuenta como 1
    assert res.sleeves == {"a": Decimal("1000.00"), "b": Decimal("0")}
    assert "horizonte" in res.excluded["b"]


# --- nocional mínimo ---------------------------------------------------------------------
def test_min_notional_drops_small_sleeve_and_redistributes(alloc):
    strategies = [stats("big", vol=0.01), stats("small", vol=1.0)]
    res = alloc.allocate_detailed(Decimal("100"), Horizon.MEDIUM, strategies)
    assert res.sleeves == {"big": Decimal("100.00"), "small": Decimal("0")}
    assert "nocional" in res.excluded["small"]


def test_capital_below_min_notional_allocates_nothing(alloc):
    res = alloc.allocate_detailed(Decimal("5"), Horizon.MEDIUM, [stats("a")])
    assert res.sleeves == {"a": Decimal("0")}
    assert res.unallocated == Decimal("5")


# --- casos borde -------------------------------------------------------------------------
def test_single_strategy_gets_everything(alloc):
    assert alloc.allocate(Decimal("1000"), Horizon.LONG, [stats("a")]) == {"a": Decimal("1000.00")}


def test_no_strategies(alloc):
    res = alloc.allocate_detailed(Decimal("1000"), Horizon.MEDIUM, [])
    assert res.sleeves == {}
    assert res.unallocated == Decimal("1000")


def test_zero_capital(alloc):
    assert alloc.allocate(Decimal("0"), Horizon.MEDIUM, [stats("a")]) == {"a": Decimal("0")}


@pytest.mark.parametrize("vol", [0.0, float("inf")])
def test_zero_or_infinite_volatility_is_excluded(alloc, vol):
    res = alloc.allocate_detailed(
        Decimal("1000"), Horizon.MEDIUM, [stats("a"), stats("z", vol=vol)]
    )
    assert res.sleeves == {"a": Decimal("1000.00"), "z": Decimal("0")}
    assert "volatilidad" in res.excluded["z"]


@pytest.mark.parametrize("sharpe", [0.0, -0.3, float("nan")])
def test_non_positive_sharpe_is_excluded(alloc, sharpe):
    res = alloc.allocate_detailed(
        Decimal("1000"), Horizon.MEDIUM, [stats("a"), stats("z", sharpe=sharpe)]
    )
    assert res.sleeves["z"] == 0
    assert "Sharpe" in res.excluded["z"]


def test_profile_max_drawdown_excludes_risky_strategy(alloc):
    strategies = [stats("ok", max_drawdown_p95=0.10), stats("risky", max_drawdown_p95=0.25)]
    res = alloc.allocate_detailed(
        Decimal("1000"), Horizon.MEDIUM, strategies, max_drawdown=Decimal("0.20")
    )
    assert res.sleeves == {"ok": Decimal("1000.00"), "risky": Decimal("0")}
    assert "drawdown" in res.excluded["risky"]
    # Sin límite del perfil, ambas reciben capital
    assert alloc.allocate(Decimal("1000"), Horizon.MEDIUM, strategies)["risky"] > 0


def test_invalid_inputs(alloc):
    with pytest.raises(ValueError, match="únicos"):
        alloc.allocate(Decimal("1"), Horizon.MEDIUM, [stats("a"), stats("a")])
    with pytest.raises(ValueError, match="profile_capital"):
        alloc.allocate(Decimal("-1"), Horizon.MEDIUM, [stats("a")])
    with pytest.raises(ValueError, match="profile_capital"):
        alloc.allocate(Decimal("NaN"), Horizon.MEDIUM, [stats("a")])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"correlation_penalty": -1.0},
        {"correlation_penalty": float("inf")},
        {"min_notional": Decimal("-1")},
        {"quantum": Decimal("0")},
        {"horizon_weights": {Horizon.SHORT: {Horizon.SHORT: -1.0}}},
    ],
)
def test_invalid_allocator_config(kwargs):
    with pytest.raises(ValueError):
        PortfolioAllocator(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": ""},
        {"volatility": -0.1},
        {"volatility": float("nan")},
        {"returns": [0.1, float("nan")]},
        {"max_drawdown_p95": 1.5},
        {"state": "paused"},
    ],
)
def test_invalid_strategy_stats(kwargs):
    base = {"name": "a", "state": ACTIVE, "volatility": 0.1, "oos_sharpe": 1.0}
    with pytest.raises(ValueError):
        StrategyStats(**{**base, **kwargs})


def test_strategy_stats_normalizes_returns():
    s = stats("a", returns=np.array([0.01, 0.02]))
    assert s.returns == (0.01, 0.02)


# --- ApprovalCriteria.for_profile --------------------------------------------------------
def test_approval_criteria_for_profile():
    c = ApprovalCriteria.for_profile(Decimal("0.15"))
    assert c.max_drawdown_p95 == pytest.approx(0.15)
    assert (c.min_oos_sharpe, c.min_trades) == (0.5, 100)
    c = ApprovalCriteria.for_profile(0.3, min_oos_sharpe=1.0, min_trades=50)
    assert (c.max_drawdown_p95, c.min_oos_sharpe, c.min_trades) == (0.3, 1.0, 50)
    with pytest.raises(ValueError):
        ApprovalCriteria.for_profile(0)
