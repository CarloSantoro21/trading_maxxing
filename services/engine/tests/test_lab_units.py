"""Piezas del laboratorio (#13): ventanas, métricas y simulador vectorizado."""

from __future__ import annotations

import itertools
import math

import numpy as np
import pandas as pd
import pytest
from tm_engine.lab.config import HoldoutError, WalkForwardConfig, utc
from tm_engine.lab.metrics import (
    daily_returns,
    max_drawdown,
    perf_stats,
    sharpe_ratio,
    window_slice,
)
from tm_engine.lab.simulator import (
    buy_and_hold,
    daily_equity,
    floor_qty,
    simulate_sleeve,
    slice_bars,
)
from tm_shared.contracts import OrderSide

DAY = pd.Timedelta(days=1)


# --- ventanas ------------------------------------------------------------------------------


def test_default_windows_are_16_quarters_with_12_months_in_sample():
    windows = WalkForwardConfig(strategy="trend").windows()
    assert len(windows) == 16
    assert [w.label for w in windows[:2]] == ["2021-T1", "2021-T2"]
    assert windows[-1].label == "2024-T4"
    first, last = windows[0], windows[-1]
    assert (first.is_start, first.is_end) == (utc("2020-01-01"), utc("2021-01-01"))
    assert (last.is_start, last.oos_start, last.oos_end) == (
        utc("2023-10-01"),
        utc("2024-10-01"),
        utc("2025-01-01"),
    )
    for a, b in itertools.pairwise(windows):
        assert b.oos_start == a.oos_end  # los tramos OOS se pegan sin huecos ni traslapes
    assert first.to_dict()["label"] == "2021-T1"


def test_window_sizes_are_configurable_and_clip_to_data_start():
    cfg = WalkForwardConfig(
        strategy="trend", oos_start="2020-03-01", oos_end="2020-06-01", is_months=6, oos_months=1
    )
    windows = cfg.windows()
    assert [w.label for w in windows] == ["2020-03", "2020-04", "2020-05"]
    assert windows[0].is_start == utc("2020-01-01")  # no hay datos antes de data_start
    assert windows[2].is_start == utc("2020-01-01")


def test_windows_crossing_the_holdout_need_the_flag():
    cfg = WalkForwardConfig(strategy="trend", oos_end="2025-04-01")
    with pytest.raises(HoldoutError, match="holdout"):
        cfg.windows()
    assert cfg.data_end == utc("2025-01-01")
    holdout = WalkForwardConfig(strategy="trend").holdout()
    assert holdout.include_holdout
    assert [w.label for w in holdout.windows()] == ["2025-T1", "2025-T2", "2025-T3", "2025-T4"]
    assert holdout.data_end == utc("2026-01-01")
    assert holdout.to_dict()["include_holdout"] is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {"is_months": 0},
        {"symbols": ()},
        {"capital": 0},
        {"fee": 0.5},
        {"engine": "otro"},
        {"oos_start": "2022-01-01", "oos_end": "2021-01-01"},
    ],
)
def test_invalid_configs_are_rejected(kwargs):
    with pytest.raises(ValueError):
        WalkForwardConfig(strategy="trend", **kwargs)


# --- métricas ------------------------------------------------------------------------------


def _daily(values, start="2024-01-01"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="D", tz="UTC"))


def test_sharpe_uses_365_days():
    rets = np.array([0.01, -0.005, 0.02, 0.0])
    expected = rets.mean() / rets.std(ddof=1) * math.sqrt(365)
    assert sharpe_ratio(rets) == pytest.approx(expected)
    assert sharpe_ratio(np.zeros(10)) is None
    assert sharpe_ratio(np.array([0.1])) is None


def test_max_drawdown_and_stats_on_known_curve():
    eq = _daily([100, 120, 90, 110, 130])
    assert max_drawdown(eq) == pytest.approx(0.25)
    assert max_drawdown(np.array([])) == 0.0
    stats = perf_stats(eq)
    assert stats.total_return == pytest.approx(0.30)
    assert stats.days == 4
    assert stats.max_drawdown == pytest.approx(0.25)
    assert stats.sharpe == pytest.approx(sharpe_ratio(daily_returns(eq)))
    assert stats.cagr == pytest.approx(1.3 ** (365 / 4) - 1)
    assert stats.to_dict()["final_equity"] == 130
    with pytest.raises(ValueError):
        perf_stats(eq.iloc[:1])
    assert list(window_slice(eq, eq.index[1], eq.index[2])) == [120, 90]


# --- simulador -----------------------------------------------------------------------------


def test_floor_qty_never_rounds_up():
    assert floor_qty(1.234567, 5) == 1.23456
    assert floor_qty(0.3 * 3, 1) == 0.9  # tolera el error de punto flotante


def _bars(closes, start="2024-01-01"):
    idx = pd.date_range(start, periods=len(closes), freq="D", tz="UTC", name="open_time")
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1.0}, index=idx)


def test_simulated_round_trip_has_exact_pnl_and_fees():
    bars = _bars([100, 100, 110, 121, 121])
    target = pd.Series([0, 1, 1, 0, 0], index=bars.index)
    run = simulate_sleeve(
        bars, target, capital=1000, fee=0.001, period=DAY, sleeve="s", symbol="X",
        qty_decimals=2, cash_buffer=0.0,
    )  # fmt: skip
    qty = floor_qty(1000 / (100 * 1.001), 2)  # 9.99
    assert qty == 9.99
    buy, sell = run.orders
    assert (buy.side, buy.price, buy.quantity) == (OrderSide.BUY, 100, qty)
    assert buy.ts == bars.index[1] + DAY  # se llena al cierre de la vela de la señal
    assert (sell.side, sell.price) == (OrderSide.SELL, 121)
    fees = qty * 100 * 0.001 + qty * 121 * 0.001
    assert run.commissions == pytest.approx(fees)
    (trade,) = run.trades
    pnl = qty * (121 - 100) - fees
    assert trade.pnl == pytest.approx(pnl)
    assert trade.ret == pytest.approx(pnl / (qty * 100 * 1.001))
    assert trade.duration_days == 2
    assert not trade.is_open
    # equity a mercado vela por vela
    cash = 1000 - qty * 100 * 1.001
    expected = [1000, cash + qty * 100, cash + qty * 110, 1000 + pnl, 1000 + pnl]
    assert run.equity.to_numpy() == pytest.approx(expected)


def test_open_position_is_marked_to_market_at_the_end():
    bars = _bars([100, 100, 150])
    run = simulate_sleeve(
        bars, pd.Series(1.0, index=bars.index), capital=100, fee=0.0, period=DAY, sleeve="s",
        symbol="X", qty_decimals=3, cash_buffer=0.0,
    )  # fmt: skip
    (trade,) = run.trades
    assert trade.is_open and trade.duration_days is None
    assert trade.pnl == pytest.approx(50)
    assert run.equity.iloc[-1] == pytest.approx(150)


def test_simulator_handles_no_cash_and_empty_bars():
    bars = _bars([100, 100])
    run = simulate_sleeve(
        bars, pd.Series(1.0, index=bars.index), capital=0.001, fee=0.001, period=DAY,
        sleeve="s", symbol="BTCUSDT",
    )  # fmt: skip
    assert run.orders == [] and run.trades == []
    empty = simulate_sleeve(
        bars.iloc[:0], pd.Series(dtype=float), capital=1, fee=0, period=DAY, sleeve="s",
        symbol="X",
    )  # fmt: skip
    assert empty.equity.empty


def test_daily_equity_sums_sleeves_and_starts_at_capital():
    bars = _bars([100, 100, 110, 121])
    a = simulate_sleeve(
        bars, pd.Series(1.0, index=bars.index), capital=1000, fee=0.0, period=DAY, sleeve="a",
        symbol="X", qty_decimals=4, cash_buffer=0.0,
    )  # fmt: skip
    b = simulate_sleeve(
        bars, pd.Series(0.0, index=bars.index), capital=500, fee=0.0, period=DAY, sleeve="b",
        symbol="X",
    )  # fmt: skip
    start = bars.index[0]
    eq = daily_equity([a, b], start, start + 4 * DAY)
    assert eq.iloc[0] == 1500
    assert eq.iloc[-1] == pytest.approx(1000 * 1.21 + 500)
    assert len(eq) == 5


def test_buy_and_hold_splits_capital_and_pays_one_fee():
    frames = {"X": _bars([100, 100, 200]), "Y": _bars([50, 50, 50])}
    start = frames["X"].index[0]
    runs = buy_and_hold(
        frames, capital=2000, fee=0.001, period=DAY, start=start, end=start + 3 * DAY
    )
    eq = daily_equity(runs, start, start + 3 * DAY)
    assert eq.iloc[-1] == pytest.approx(1000 / 1.001 * 2 + 1000 / 1.001, rel=1e-4)
    assert all(len(r.orders) == 1 for r in runs)


def test_slice_bars_uses_the_close_time():
    bars = _bars([1, 2, 3, 4])
    start = bars.index[1]
    out = slice_bars(bars, start, start + 2 * DAY, DAY)
    assert list(out["close"]) == [2, 3]  # las velas que cierran en (start, end]
