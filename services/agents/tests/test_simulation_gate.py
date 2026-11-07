"""Pruebas del SimulationGate con trayectorias deterministas de resultado conocido."""

import time
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from tm_agents.simulation_gate import (
    GateConfig,
    Levels,
    PathSet,
    SimulationGate,
    attach_sim,
    bootstrap_paths,
    combine,
    simulate,
)
from tm_shared.contracts import OrderSide, Rationale, SimResult, TradeDecision

BUY = Levels(side=OrderSide.BUY, entry_price=100.0, stop_loss=95.0, take_profit=110.0)  # 2R


def path_set(high, low, close, source="test", weights=None):
    return PathSet(
        high=np.array(high, dtype=np.float64),
        low=np.array(low, dtype=np.float64),
        close=np.array(close, dtype=np.float64),
        source=source,
        weights=None if weights is None else np.array(weights, dtype=np.float64),
    )


def make_bars(days=70, freq="15min", seed=0, start="2025-01-01"):
    idx = pd.date_range(start, periods=int(days * 24 * 60 / 15), freq=freq, tz="UTC")
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, len(idx))))
    spread = np.abs(rng.normal(0, 0.001, len(idx))) * close
    return pd.DataFrame(
        {"open": close, "high": close + spread, "low": close - spread, "close": close},
        index=pd.DatetimeIndex(idx, name="open_time"),
    )


# --- simulate con trayectorias deterministas ---------------------------------


def test_all_paths_hit_take_profit():
    paths = path_set([[105, 111]] * 4, [[99, 104]] * 4, [[104, 110]] * 4)
    sim = simulate(BUY, paths)
    assert (sim.p_take_profit, sim.p_stop_loss, sim.p_open) == (1.0, 0.0, 0.0)
    assert sim.expected_value == pytest.approx(2.0)
    assert sim.n_paths == 4
    assert sim.source == "test"


def test_all_paths_hit_stop_loss():
    paths = path_set([[101, 102]], [[96, 94]], [[97, 95]])
    sim = simulate(BUY, paths)
    assert sim.p_stop_loss == 1.0
    assert sim.expected_value == pytest.approx(-1.0)


def test_known_mix_of_outcomes():
    # 1 TP, 2 SL, 1 abierta que cierra en 102.5 (+0.5R)
    high = [[105, 112], [101, 100], [103, 101], [103, 104]]
    low = [[99, 101], [94, 93], [97, 90], [98, 101]]
    close = [[104, 111], [95, 94], [98, 91], [101, 102.5]]
    sim = simulate(BUY, path_set(high, low, close))
    assert sim.p_take_profit == pytest.approx(0.25)
    assert sim.p_stop_loss == pytest.approx(0.5)
    assert sim.p_open == pytest.approx(0.25)
    assert sim.expected_value == pytest.approx((2.0 - 1.0 - 1.0 + 0.5) / 4)


def test_first_touch_wins_over_later_touch():
    # TP en la vela 0 y SL en la vela 1 -> TP; al revés -> SL
    tp_then_sl = path_set([[111, 100]], [[101, 90]], [[109, 91]])
    sl_then_tp = path_set([[101, 115]], [[94, 100]], [[96, 112]])
    assert simulate(BUY, tp_then_sl).p_take_profit == 1.0
    assert simulate(BUY, sl_then_tp).p_stop_loss == 1.0


def test_same_bar_touching_both_counts_as_stop_loss():
    paths = path_set([[111]], [[94]], [[105]])
    sim = simulate(BUY, paths)
    assert sim.p_stop_loss == 1.0
    assert sim.expected_value == pytest.approx(-1.0)


def test_sell_side_is_mirrored():
    sell = Levels(side=OrderSide.SELL, entry_price=100.0, stop_loss=105.0, take_profit=90.0)
    tp = path_set([[101, 100]], [[95, 89]], [[96, 90]])
    sl = path_set([[106]], [[99]], [[105]])
    still = path_set([[102]], [[97]], [[97.5]])
    assert simulate(sell, tp).expected_value == pytest.approx(2.0)
    assert simulate(sell, sl).p_stop_loss == 1.0
    assert simulate(sell, still).expected_value == pytest.approx(0.5)


def test_weights_change_probabilities():
    high = [[111], [101]]
    low = [[99], [94]]
    close = [[110], [95]]
    sim = simulate(BUY, path_set(high, low, close, weights=[3, 1]))
    assert sim.p_take_profit == pytest.approx(0.75)
    assert sim.expected_value == pytest.approx(0.75 * 2 - 0.25)


def test_fee_reduces_expected_value_in_r():
    paths = path_set([[111]], [[99]], [[110]])
    sim = simulate(BUY, paths, fee_rate=0.001)
    # 2 * 0.001 * 100 / 5 = 0.04R
    assert sim.expected_value == pytest.approx(2.0 - 0.04)


def test_trade_decision_with_decimal_and_entry_override():
    decision = TradeDecision(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity=Decimal("0.01"),
        stop_loss=Decimal("95"),
        take_profit=Decimal("110"),
    )
    paths = path_set([[111]], [[99]], [[110]])
    with pytest.raises(ValueError, match="entry_price"):
        simulate(decision, paths)
    assert simulate(decision, paths, entry=100.0).p_take_profit == 1.0


@pytest.mark.parametrize(
    ("levels", "match"),
    [
        (Levels(OrderSide.BUY, 100.0, 101.0, 110.0), "compra"),
        (Levels(OrderSide.SELL, 100.0, 95.0, 90.0), "venta"),
        (Levels(OrderSide.BUY, float("nan"), 95.0, 110.0), "finitos"),
    ],
)
def test_simulate_rejects_inconsistent_levels(levels, match):
    with pytest.raises(ValueError, match=match):
        simulate(levels, path_set([[100]], [[100]], [[100]]))


def test_simulate_rejects_bad_fee():
    with pytest.raises(ValueError, match="fee_rate"):
        simulate(BUY, path_set([[100]], [[100]], [[100]]), fee_rate=0.5)


# --- PathSet y combine --------------------------------------------------------


def test_pathset_validation():
    with pytest.raises(ValueError, match="misma forma"):
        path_set([[1, 2]], [[1]], [[1]])
    with pytest.raises(ValueError, match="positivos"):
        path_set([[1]], [[0]], [[1]])
    with pytest.raises(ValueError, match="high < low"):
        path_set([[1]], [[2]], [[1.5]])
    with pytest.raises(ValueError, match="weights"):
        path_set([[1]], [[1]], [[1]], weights=[-1])
    with pytest.raises(ValueError, match="al menos una"):
        PathSet(high=np.empty((0, 1)), low=np.empty((0, 1)), close=np.empty((0, 1)), source="x")


def test_from_array_close_only_and_ohlcv():
    closes = np.array([[101.0, 111.0], [99.0, 94.0]])
    ps = PathSet.from_array(closes)
    assert ps.source == "kronos"
    assert simulate(BUY, ps).p_take_profit == pytest.approx(0.5)

    # (samples, pasos, OHLCV) como Forecast.paths; high incoherente se corrige con open/close
    ohlcv = np.array([[[100, 100, 99, 111, 1.0]]])
    ps3 = PathSet.from_array(ohlcv, source="k")
    assert ps3.high[0, 0] == 111
    with pytest.raises(ValueError, match="forma"):
        PathSet.from_array(np.ones(3))


def test_combine_weights_per_source():
    boot = path_set([[101]] * 8, [[94]] * 8, [[95]] * 8, source="bootstrap")  # todo SL
    ext = path_set([[111, 112]] * 2, [[99, 99]] * 2, [[110, 110]] * 2)  # todo TP
    combo = combine(boot, ext, weight=0.25)
    sim = simulate(BUY, combo)
    assert combo.source == "combined"
    assert combo.steps == 1
    assert sim.n_paths == 10
    assert sim.p_take_profit == pytest.approx(0.25)
    assert sim.expected_value == pytest.approx(0.25 * 2 - 0.75)
    with pytest.raises(ValueError, match="weight"):
        combine(boot, ext, weight=1.5)


# --- bootstrap histórico -------------------------------------------------------


def test_bootstrap_is_reproducible_and_shaped():
    bars = make_bars()
    a = bootstrap_paths(bars, entry=100.0, horizon=16, n_paths=500, seed=7)
    b = bootstrap_paths(bars, entry=100.0, horizon=16, n_paths=500, seed=7)
    c = bootstrap_paths(bars, entry=100.0, horizon=16, n_paths=500, seed=8)
    assert a.close.shape == (500, 16)
    assert a.source == "bootstrap"
    np.testing.assert_array_equal(a.close, b.close)
    assert not np.array_equal(a.close, c.close)
    assert np.all(a.high >= a.low)


def test_bootstrap_uses_same_hour_of_day():
    # Velas de 1h: a las 10:00 UTC el precio siempre sube 1 %, el resto queda plano.
    idx = pd.date_range("2025-01-01", periods=24 * 70, freq="1h", tz="UTC")
    factor = np.where(idx.hour == 10, 1.01, 1.0)
    close = 100 * np.cumprod(factor)
    bars = pd.DataFrame({"high": close, "low": close, "close": close}, index=idx)
    start = pd.Timestamp("2025-03-11 09:00", tz="UTC")
    ps = bootstrap_paths(bars, entry=100.0, horizon=3, n_paths=200, seed=1, start=start)
    # Pasos: 09:00 (plano), 10:00 (+1 %), 11:00 (plano)
    np.testing.assert_allclose(ps.close[:, 0], 100.0)
    np.testing.assert_allclose(ps.close[:, 1], 101.0)
    np.testing.assert_allclose(ps.close[:, 2], 101.0)


def test_bootstrap_respects_lookback_window():
    # Antes de la ventana de 60 días el precio cae; dentro de la ventana es plano.
    idx = pd.date_range("2025-01-01", periods=24 * 100, freq="1h", tz="UTC")
    start = idx[-1] + pd.Timedelta(hours=1)
    factor = np.where(idx < start - pd.Timedelta(days=59), 0.99, 1.0)
    close = 100 * np.cumprod(factor)
    bars = pd.DataFrame({"high": close, "low": close, "close": close}, index=idx)
    ps = bootstrap_paths(bars, entry=50.0, horizon=24, n_paths=100, seed=0, lookback_days=58)
    np.testing.assert_allclose(ps.close, 50.0)


def test_bootstrap_falls_back_to_full_pool_for_missing_hour():
    idx = pd.date_range("2025-01-01", periods=24 * 10, freq="1h", tz="UTC")
    idx = idx[idx.hour != 3]
    close = np.full(len(idx), 100.0)
    bars = pd.DataFrame({"high": close + 1, "low": close - 1, "close": close}, index=idx)
    start = pd.Timestamp("2025-01-11 03:00", tz="UTC")
    ps = bootstrap_paths(bars, entry=100.0, horizon=2, n_paths=50, seed=0, start=start)
    assert np.all(np.isfinite(ps.close))


def test_bootstrap_accepts_naive_index():
    bars = make_bars(days=5)
    bars.index = bars.index.tz_localize(None)
    ps = bootstrap_paths(bars, entry=100.0, horizon=4, n_paths=10, seed=0)
    assert ps.close.shape == (10, 4)


@pytest.mark.parametrize(
    ("kwargs", "exc", "match"),
    [
        ({"horizon": 0}, ValueError, "horizon"),
        ({"n_paths": 1.5}, TypeError, "n_paths"),
        ({"entry": -1.0}, ValueError, "entry"),
        ({"seed": "x"}, TypeError, "seed"),
    ],
)
def test_bootstrap_argument_validation(kwargs, exc, match):
    params = {"entry": 100.0, "horizon": 4, "n_paths": 10, "seed": 0}
    params.update(kwargs)
    with pytest.raises(exc, match=match):
        bootstrap_paths(make_bars(days=3), **params)


def test_bootstrap_bars_validation():
    with pytest.raises(ValueError, match="columnas"):
        bootstrap_paths(make_bars(days=3)[["close"]], entry=100.0, horizon=2)
    with pytest.raises(ValueError, match="DatetimeIndex"):
        bootstrap_paths(pd.DataFrame({"high": [1], "low": [1], "close": [1]}), entry=1.0, horizon=1)
    with pytest.raises(ValueError, match="insuficiente"):
        bootstrap_paths(make_bars(days=0.2), entry=100.0, horizon=2)
    dup = make_bars(days=3)
    dup.index = pd.DatetimeIndex([dup.index[0]] * len(dup))
    with pytest.raises(ValueError, match="duplicados"):
        bootstrap_paths(dup, entry=100.0, horizon=2)


def test_bootstrap_with_generator_seed():
    rng = np.random.default_rng(3)
    ps = bootstrap_paths(make_bars(days=5), entry=100.0, horizon=4, n_paths=10, seed=rng)
    assert ps.n_paths == 10


def test_10k_paths_under_one_second():
    bars = make_bars(days=60)
    gate = SimulationGate()
    gate.historical_paths(bars, entry=100.0, horizon=4)  # calentamiento
    t0 = time.perf_counter()
    paths = gate.historical_paths(bars, entry=100.0, horizon=96)
    sim = gate.simulate(BUY, paths)
    elapsed = time.perf_counter() - t0
    assert sim.n_paths == 10_000
    assert elapsed < 1.0, f"10k trayectorias tardaron {elapsed:.3f} s"


# --- SimulationGate ------------------------------------------------------------


def _sim(ev):
    return SimResult(
        p_take_profit=0.5, p_stop_loss=0.5, p_open=0.0, expected_value=ev, n_paths=1, source="x"
    )


def test_approve_blocks_non_positive_expected_value():
    gate = SimulationGate()
    assert gate.approve(_sim(0.1)).approved
    for ev in (0.0, -0.3):
        verdict = gate.approve(_sim(ev))
        assert not verdict.approved
        assert "valor esperado" in verdict.reasons[0]


def test_approve_with_custom_threshold():
    gate = SimulationGate(GateConfig(min_expected_value=0.2))
    assert not gate.approve(_sim(0.1)).approved
    assert gate.approve(_sim(0.3)).approved


@pytest.mark.parametrize(
    "kwargs",
    [
        {"n_paths": 0},
        {"external_weight": 2.0},
        {"fee_rate": -0.1},
        {"min_expected_value": float("inf")},
    ],
)
def test_gate_config_validation(kwargs):
    with pytest.raises((ValueError, TypeError)):
        GateConfig(**kwargs)


def test_build_paths_without_and_with_kronos():
    bars = make_bars(days=20)
    gate = SimulationGate(GateConfig(n_paths=1_000, external_weight=0.4))
    only_boot = gate.build_paths(bars, entry=100.0, horizon=8)
    assert only_boot.source == "bootstrap"
    assert only_boot.n_paths == 1_000

    kronos = np.full((20, 8), 120.0)  # todas las trayectorias de Kronos tocan el TP
    combo = gate.build_paths(bars, entry=100.0, horizon=8, external=kronos)
    assert combo.source == "combined"
    assert combo.n_paths == 1_020
    sim_boot = gate.simulate(BUY, only_boot)
    sim_combo = gate.simulate(BUY, combo)
    assert sim_combo.p_take_profit == pytest.approx(0.4 + 0.6 * sim_boot.p_take_profit)

    combo2 = gate.build_paths(
        bars, entry=100.0, horizon=8, external=PathSet.from_array(kronos, source="kronos")
    )
    np.testing.assert_array_equal(combo.weights, combo2.weights)


def test_evaluate_stores_sim_in_rationale():
    decision = TradeDecision(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity=Decimal("0.01"),
        entry_price=Decimal("100"),
        stop_loss=Decimal("95"),
        take_profit=Decimal("110"),
        rationale=Rationale(news_summary="ok"),
    )
    paths = path_set([[111]], [[99]], [[110]])
    gate = SimulationGate()
    updated, verdict = gate.evaluate(decision, paths)
    assert verdict.approved
    assert updated.rationale.sim is not None
    assert updated.rationale.sim.expected_value == pytest.approx(2.0)
    assert updated.rationale.news_summary == "ok"
    assert decision.rationale.sim is None
    # Serializa a JSON para trades.rationale
    assert "expected_value" in updated.model_dump_json()
    assert attach_sim(decision, updated.rationale.sim) == updated
