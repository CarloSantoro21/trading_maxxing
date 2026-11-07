"""Pruebas del LifecycleMonitor con series en vivo sintéticas."""

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from tm_agents.lifecycle import (
    LifecycleMonitor,
    LifecycleThresholds,
    LiveStats,
    TradeRecord,
    recent_sharpe,
)
from tm_agents.montecarlo import reshuffle
from tm_agents.strategy_stats import StrategyState

T0 = datetime(2026, 1, 1, tzinfo=UTC)
ACTIVE, PROB = StrategyState.ACTIVE, StrategyState.PROBATION


@pytest.fixture(scope="module")
def mc():
    trades = np.random.default_rng(1).normal(0.004, 0.02, size=300)
    return reshuffle(trades, 2_000, seed=7, initial_capital=10_000)


def trades(rets):
    return [TradeRecord(r, T0 + timedelta(hours=i)) for i, r in enumerate(rets)]


def live(rets, equity=10_000.0, peak=10_000.0, days=5.0, dd=None):
    return LiveStats(equity, peak, trades(rets), T0, T0 + timedelta(days=days), dd)


def noisy(mean, n=30, seed=3, sd=0.02):
    return list(np.random.default_rng(seed).normal(mean, sd, n))


def test_normal_no_transition(mc):
    s = live(noisy(0.006), equity=10_300, peak=10_400)
    assert LifecycleMonitor().check(ACTIVE, s, mc) is None


def test_bad_streak_within_expected_does_not_degrade(mc):
    dd = mc.max_drawdown.p50  # caída típica, bajo el p95
    assert dd < mc.max_drawdown.p95
    s = live(noisy(0.006), equity=10_000 * (1 - dd))
    assert LifecycleMonitor().check(ACTIVE, s, mc) is None


def test_real_deterioration_by_drawdown(mc):
    dd = mc.max_drawdown.p95 + 0.05
    t = LifecycleMonitor().check(ACTIVE, live(noisy(0.006), equity=10_000 * (1 - dd)), mc)
    assert t is not None
    assert (t.from_state, t.to_state, t.actor) == (ACTIVE, PROB, "system")
    assert (t.from_, t.to) == (ACTIVE, PROB)
    assert "p95" in t.reason and f"{dd:.1%}" in t.reason


def test_real_deterioration_by_sharpe(mc):
    t = LifecycleMonitor().check(ACTIVE, live(noisy(-0.01), equity=9_900), mc)
    assert t is not None and t.to_state is PROB
    assert "Sharpe" in t.reason


def test_fewer_than_window_trades_no_signal(mc):
    assert recent_sharpe(trades([-0.05] * 29), 30) is None
    assert LifecycleMonitor().check(ACTIVE, live([-0.05] * 29, equity=9_990), mc) is None


def test_sharpe_uses_only_last_window():
    rets = [-0.05] * 20 + noisy(0.01)
    assert recent_sharpe(trades(rets), 30) > 0


def test_sharpe_edge_cases():
    assert recent_sharpe(trades([0.01] * 30), 30) == math.inf
    assert recent_sharpe(trades([-0.01] * 30), 30) == -math.inf
    assert recent_sharpe(trades([0.0] * 30), 30) == 0.0
    assert recent_sharpe(trades([0.01] * 29 + [math.nan]), 30) is None
    assert recent_sharpe(trades([0.01] * 3), 1) is None


def test_probation_to_retired_by_p99(mc):
    dd = mc.max_drawdown.p99 + 0.02
    t = LifecycleMonitor().check(PROB, live(noisy(0.0), equity=10_000 * (1 - dd)), mc)
    assert t is not None and t.to_state is StrategyState.RETIRED
    assert "p99" in t.reason


def test_probation_retired_after_30_days_without_recovery(mc):
    dd = (mc.max_drawdown.p95 + mc.max_drawdown.p99) / 2
    s = live(noisy(0.0), equity=10_000 * (1 - dd), days=31)
    t = LifecycleMonitor().check(PROB, s, mc)
    assert t is not None and t.to_state is StrategyState.RETIRED
    assert "31 días" in t.reason


def test_probation_without_sharpe_signal_reports_it(mc):
    dd = (mc.max_drawdown.p95 + mc.max_drawdown.p99) / 2
    s = live([], equity=10_000 * (1 - dd), days=31)
    t = LifecycleMonitor().check(PROB, s, mc)
    assert t is not None and "sin señal" in t.reason


def test_probation_that_recovers_stays_and_never_goes_back_to_active(mc):
    s = live(noisy(0.006), equity=10_100, peak=10_200, days=45)
    assert LifecycleMonitor().check(PROB, s, mc) is None


def test_probation_waits_before_30_days(mc):
    dd = (mc.max_drawdown.p95 + mc.max_drawdown.p99) / 2
    s = live(noisy(-0.01), equity=10_000 * (1 - dd), days=10)
    assert LifecycleMonitor().check(PROB, s, mc) is None


def test_probation_negative_sharpe_after_30_days_retires(mc):
    s = live(noisy(-0.01), equity=9_950, peak=10_000, days=40)
    t = LifecycleMonitor().check(PROB, s, mc)
    assert t is not None and t.to_state is StrategyState.RETIRED


@pytest.mark.parametrize(
    "state",
    [StrategyState.RESEARCH, StrategyState.LAB, StrategyState.SHADOW, StrategyState.RETIRED],
)
def test_other_states_never_transition(mc, state):
    assert LifecycleMonitor().check(state, live(noisy(-0.02), equity=1_000), mc) is None


def test_configurable_thresholds(mc):
    th = LifecycleThresholds(sharpe_window=10, min_sharpe=0.5, probation_days=5)
    mon = LifecycleMonitor(th)
    t = mon.check(ACTIVE, live(noisy(0.0005, n=10)), mc)
    assert t is not None and "últimos 10 trades" in t.reason
    dd = (mc.max_drawdown.p95 + mc.max_drawdown.p99) / 2
    r = mon.check(PROB, live(noisy(0.006, n=10), equity=10_000 * (1 - dd), days=6), mc)
    assert r is not None and r.to_state is StrategyState.RETIRED


def test_deterministic(mc):
    s = live(noisy(-0.01), equity=9_000)
    mon = LifecycleMonitor()
    assert mon.check(ACTIVE, s, mc) == mon.check(ACTIVE, s, mc)


def test_drawdown_properties():
    assert live([], equity=9_000, peak=10_000).drawdown == pytest.approx(0.1)
    assert live([], equity=1, peak=0).drawdown == 0.0
    assert live([], equity=11_000, peak=10_000).drawdown == 0.0
    assert live([], dd=0.3).drawdown == 0.3
    assert live([], days=2).days_in_state == 2.0


@pytest.mark.parametrize(
    "kw", [{"sharpe_window": 1}, {"probation_days": -1}, {"recovery_drawdown_fraction": 0}]
)
def test_invalid_thresholds(kw):
    with pytest.raises(ValueError):
        LifecycleThresholds(**kw)
