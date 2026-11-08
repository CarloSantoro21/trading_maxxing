"""walk_forward (#13): equivalencia con los agentes, resultados conocidos, holdout y Nautilus."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from tm_agents.base import AgentContext
from tm_agents.strategies import IntradayMomentumAgent, TrendAgent
from tm_engine.lab import HoldoutError, WalkForwardConfig, walk_forward
from tm_engine.lab.execution import equity_from_fills, run_nautilus, to_envelope
from tm_engine.lab.simulator import simulate_sleeve
from tm_engine.lab.strategies import (
    IntradayMomentumAdapter,
    TrendAdapter,
    get_adapter,
    param_key,
)
from tm_shared.contracts import Direction, OrderSide, Timeframe

DAY = pd.Timedelta(days=1)
M30 = pd.Timedelta(minutes=30)


def _cycle(t):
    """Tendencias largas alternadas: sube 200 días y baja 100 (resultado conocido)."""
    return 100 * np.exp(0.004 * np.where((t // 150) % 2 == 0, t % 150, 150 - t % 150) + 0.001 * t)


# --- equivalencia con los agentes ----------------------------------------------------------


def test_trend_targets_match_trend_agent(daily_bars):
    bars = daily_bars("2020-01-01", "2021-06-01", _cycle)
    adapter = TrendAdapter()
    fitted = adapter.fit({"lookbacks": [10, 20, 40, 80]}, bars)
    targets = adapter.targets(bars, fitted)
    for t in (30, 120, 200, 260, 400, 500):
        now = (bars.index[t] + DAY).to_pydatetime()
        ctx = AgentContext(symbol="BTCUSDT", now=now, bars={Timeframe.D1: bars})
        for n, target in zip(fitted["lookbacks"], targets, strict=True):
            sig = TrendAgent(lookbacks=[n]).analyze(ctx)
            assert (sig.direction is Direction.LONG) == bool(target.iloc[t]), (t, n)
    # la exposición total es la confianza (voto promedio) del agente con los cuatro periodos
    now = (bars.index[260] + DAY).to_pydatetime()
    ctx = AgentContext(symbol="BTCUSDT", now=now, bars={Timeframe.D1: bars})
    sig = TrendAgent(lookbacks=fitted["lookbacks"]).analyze(ctx)
    assert sig.confidence == pytest.approx(np.mean([t.iloc[260] for t in targets]))
    assert adapter.describe(fitted) == "10/20/40/80"
    with pytest.raises(ValueError):
        adapter.fit({"lookbacks": [10, 20]}, bars)


def test_intraday_targets_match_intraday_agent(intraday_bars):
    bars = intraday_bars("2024-02-01", "2024-04-15", seed=3)
    adapter = IntradayMomentumAdapter()
    for params in ({"mode": "onfh", "threshold": 0.0}, {"mode": "both", "threshold": 0.002}):
        fitted = adapter.fit({**params, "open_slot": 19}, bars)
        (target,) = adapter.targets(bars, fitted)
        agent = IntradayMomentumAgent(open_time="09:30", **params)
        longs = 0
        for day in pd.date_range("2024-03-01", "2024-04-10", freq="D"):
            # 16:30 ET: la vela de las 16:00 acaba de cerrar (incluye un cambio de horario)
            now = pd.Timestamp(f"{day.date()} 16:30", tz="America/New_York").tz_convert("UTC")
            ctx = AgentContext(
                symbol="BTCUSDT", now=now.to_pydatetime(), bars={Timeframe.M30: bars}
            )
            sig = agent.analyze(ctx)
            entry_bar = now - M30
            assert (sig.direction is Direction.LONG) == bool(target.loc[entry_bar]), day
            longs += sig.direction is Direction.LONG
        assert longs > 0
        assert target.sum() > 0
    assert "apertura 09:30 ET" in adapter.describe(fitted)
    # sin open_slot se ajusta con el reloj de volumen (el fixture tiene el salto a las 9:30)
    assert adapter.fit({"mode": "onfh", "threshold": 0.0}, bars)["open_slot"] == 19


def test_grids_and_adapter_lookup():
    assert len(TrendAdapter().default_grid()) == 4
    assert {"lookbacks": [20, 50, 100, 200]} in TrendAdapter().default_grid()
    grid = IntradayMomentumAdapter().default_grid()
    assert {"mode": "slh", "threshold": 0.0} in grid and len(grid) == 9
    assert isinstance(get_adapter("trend"), TrendAdapter)
    with pytest.raises(KeyError):
        get_adapter("kronos")
    assert param_key({"b": [1, 2], "a": "x"}) == "a=x;b=1/2"


# --- walk-forward --------------------------------------------------------------------------


def _trend_config(**kw):
    base = {
        "strategy": "trend",
        "symbols": ("BTCUSDT", "ETHUSDT"),
        "oos_start": "2021-01-01",
        "oos_end": "2022-01-01",
        "grid": [{"lookbacks": [10, 20, 40, 80]}, {"lookbacks": [20, 40, 80, 160]}],
        "engine": "vector",
        "n_simulations": 300,
    }
    return WalkForwardConfig(**{**base, **kw})


def _frames(daily_bars, end="2026-01-01"):
    return {
        "BTCUSDT": daily_bars("2020-01-01", end, _cycle),
        "ETHUSDT": daily_bars("2020-01-01", end, lambda t: _cycle(t + 40) / 10),
    }


def test_walk_forward_on_known_trend(daily_bars, frames_loader):
    report = walk_forward(_trend_config(), loader=frames_loader(_frames(daily_bars)))
    assert [w.window.label for w in report.windows] == ["2021-T1", "2021-T2", "2021-T3", "2021-T4"]
    agg = report.aggregate
    assert agg["days"] == 365
    assert agg["trades"] == len(report.trades) > 0
    assert agg["commissions"] == pytest.approx(report.commissions) and report.commissions > 0
    assert sum(w.trades for w in report.windows) == agg["trades"]
    assert sum(w.commissions for w in report.windows) == pytest.approx(report.commissions)
    # tendencias limpias: el trend sigue la subida y se sale en la bajada -> menos drawdown
    assert agg["max_drawdown"] < report.hold["max_drawdown"]
    assert agg["total_return"] > 0
    assert report.equity.iloc[0] == pytest.approx(100_000)
    assert set(report.per_symbol) == {"BTCUSDT", "ETHUSDT"}
    assert report.stability["distinct"] >= 1
    assert sum(report.stability["counts"].values()) == 4
    mc = report.montecarlo
    assert mc["block_size"] == 20 and mc["horizon_days"] == 365
    assert mc["max_drawdown"]["p95"] >= mc["max_drawdown"]["p50"]
    assert mc["max_drawdown_1y"] is None  # el horizonte ya es de un año
    names = [c["name"] for c in report.approval["checks"]]
    assert names == ["oos_sharpe", "max_drawdown_p95", "min_trades"]
    assert report.approved == report.approval["approved"]
    assert isinstance(report.approval["beats_buy_and_hold_sharpe"], bool)


def test_walk_forward_is_reproducible(daily_bars, frames_loader):
    frames = _frames(daily_bars)
    a = walk_forward(_trend_config(), loader=frames_loader(frames))
    b = walk_forward(_trend_config(), loader=frames_loader(frames))
    pd.testing.assert_series_equal(a.equity, b.equity)
    assert a.montecarlo == b.montecarlo


def test_flat_market_never_trades(daily_bars, frames_loader):
    frames = {"BTCUSDT": daily_bars("2020-01-01", "2023-01-01", lambda t: 100.0 + 0 * t)}
    report = walk_forward(_trend_config(symbols=("BTCUSDT",)), loader=frames_loader(frames))
    assert report.aggregate["trades"] == 0
    assert report.equity.iloc[-1] == pytest.approx(100_000)
    assert all(w.is_sharpe is None for w in report.windows)
    assert not report.approved


def test_holdout_is_not_read_without_the_flag(daily_bars, frames_loader):
    frames = _frames(daily_bars)
    loader = frames_loader(frames)
    cfg = _trend_config(oos_start="2024-01-01", oos_end="2025-01-01")
    base = walk_forward(cfg, loader=loader)
    assert all(end <= pd.Timestamp("2025-01-01", tz="UTC") for *_, end in loader.calls)

    # Si el holdout cambia por completo, el resultado de desarrollo no se mueve.
    crazy = {s: f.copy() for s, f in frames.items()}
    for f in crazy.values():
        f.loc[f.index >= "2025-01-01", ["open", "high", "low", "close"]] *= 50
    again = walk_forward(cfg, loader=frames_loader(crazy))
    pd.testing.assert_series_equal(base.equity, again.equity)

    # Un cargador que ignora el rango tampoco filtra el holdout a la estrategia.
    def leaky(symbol, tf, start, end):
        return crazy[symbol]

    leaked = walk_forward(cfg, loader=leaky)
    pd.testing.assert_series_equal(base.equity, leaked.equity)

    with pytest.raises(HoldoutError):
        walk_forward(_trend_config(oos_end="2025-04-01"), loader=loader)


def test_holdout_runs_only_with_the_flag(daily_bars, frames_loader):
    loader = frames_loader(_frames(daily_bars))
    report = walk_forward(_trend_config().holdout(), loader=loader)
    assert report.windows[0].window.label == "2025-T1"
    assert report.aggregate["start"] == "2025-01-01"
    assert any(end > pd.Timestamp("2025-01-01", tz="UTC") for *_, end in loader.calls)


def test_missing_data_raises(frames_loader, daily_bars):
    empty = {"BTCUSDT": daily_bars("2019-01-01", "2019-02-01", lambda t: t + 1.0)}
    with pytest.raises(FileNotFoundError):
        walk_forward(_trend_config(symbols=("BTCUSDT",)), loader=frames_loader(empty))


def test_intraday_walk_forward_runs_on_synthetic_data(intraday_bars, frames_loader):
    frames = {"BTCUSDT": intraday_bars("2020-01-01", "2020-05-01", seed=1)}
    cfg = WalkForwardConfig(
        strategy="intraday_momentum",
        symbols=("BTCUSDT",),
        oos_start="2020-03-01",
        oos_end="2020-05-01",
        is_months=2,
        oos_months=1,
        grid=[{"mode": "onfh", "threshold": 0.0}, {"mode": "both", "threshold": 0.0}],
        engine="nautilus",
        n_simulations=200,
    )
    report = walk_forward(cfg, loader=frames_loader(frames))
    assert len(report.windows) == 2
    assert all(w.params["open_slot"] == 19 for w in report.windows)
    assert report.aggregate["trades"] > 10
    # un trade dura media hora y paga 0.1 % por lado
    assert report.aggregate["median_trade_days"] == pytest.approx(1 / 48)
    ex = report.execution
    assert ex["n_rejections"] == 0, ex["rejections"]
    assert ex["max_rel_diff"] < 1e-6


# --- Nautilus contra el simulador ----------------------------------------------------------


def test_nautilus_matches_vectorized_simulator(daily_bars):
    """Validación en un caso pequeño: misma P&L en Nautilus y en el simulador (comisión 0.1 %)."""
    # precios con 2 decimales, como los de Binance: así la comparación es exacta
    bars = daily_bars(
        "2021-01-01", "2021-04-01", lambda t: np.round(30_000 * (1 + 0.1 * np.sin(t / 7)), 2)
    ).round(2)
    target = pd.Series(np.sin(np.arange(len(bars)) / 7) > 0, index=bars.index).astype(float)
    runs = [
        simulate_sleeve(
            bars, target, capital=50_000, fee=0.001, period=DAY, sleeve="trend_0",
            symbol="BTCUSDT",
        ),
        simulate_sleeve(
            bars, 1.0 - target, capital=50_000, fee=0.001, period=DAY, sleeve="trend_1",
            symbol="BTCUSDT",
        ),
    ]  # fmt: skip
    orders = [o for r in runs for o in r.orders]
    nt = run_nautilus(
        {"BTCUSDT": bars},
        orders,
        capital_by_sleeve={(r.sleeve, r.symbol): r.capital for r in runs},
        timeframe="1d",
        period=DAY,
        fee=0.001,
    )
    assert nt.rejections == []
    assert nt.fills == len(orders)
    by_name = {r.sleeve: r for r in nt.runs}
    for sim in runs:
        got = by_name[sim.sleeve]
        np.testing.assert_allclose(got.equity.to_numpy(), sim.equity.to_numpy(), rtol=1e-9)
        assert got.commissions == pytest.approx(sim.commissions, rel=1e-6)
        assert [t.pnl for t in got.trades] == pytest.approx([t.pnl for t in sim.trades])
        assert [t.exit_price for t in got.trades] == pytest.approx(
            [t.exit_price for t in sim.trades]
        )


def test_to_envelope_follows_spot_logic():
    from tm_engine.lab.simulator import SimOrder

    ts = pd.Timestamp("2024-01-02", tz="UTC")
    buy = to_envelope(SimOrder("trend_0", "BTCUSDT", OrderSide.BUY, ts - DAY, ts, 0.5, 40_000, 20))
    d = buy.decision
    assert buy.sleeve == "trend_0" and d.side is OrderSide.BUY
    assert d.stop_loss < 40_000 < d.take_profit  # SL/TP de catástrofe, lejos del precio
    assert d.decided_at == ts.to_pydatetime()
    sell = to_envelope(SimOrder("trend_0", "BTCUSDT", OrderSide.SELL, ts - DAY, ts, 0.5, 4e5, 1))
    assert sell.decision.take_profit < sell.decision.stop_loss
    assert float(sell.decision.stop_loss) <= 999_999


def test_equity_from_fills_groups_partial_fills():
    from tm_engine.recorder import FillRecord

    idx = pd.date_range("2024-01-01", periods=4, freq="D", tz="UTC")
    bars = pd.DataFrame({"close": [100.0, 100.0, 120.0, 130.0]}, index=idx)

    def fill(side, qty, px, day, fee):
        from decimal import Decimal

        return FillRecord(
            sleeve="s", symbol="BTCUSDT", side=side, role="x", quantity=Decimal(str(qty)),
            price=Decimal(str(px)), commission=Decimal(str(fee)), realized_pnl=Decimal(0),
            position_after=Decimal(0), client_order_id="c", ts=idx[day] + DAY,
        )  # fmt: skip

    fills = [
        fill(OrderSide.BUY, 0.5, 100, 0, 0.05),
        fill(OrderSide.BUY, 0.5, 100, 0, 0.05),
        fill(OrderSide.SELL, 1.0, 120, 2, 0.12),
        fill(OrderSide.BUY, 1.0, 130, 3, 0.13),
    ]
    run = equity_from_fills(bars, fills, sleeve="s", symbol="BTCUSDT", capital=1000, period=DAY)
    closed, still_open = run.trades
    assert closed.quantity == 1.0 and closed.entry_price == 100 and closed.exit_price == 120
    assert closed.pnl == pytest.approx(20 - 0.22)
    assert still_open.is_open
    assert run.equity.iloc[-1] == pytest.approx(1000 + 20 - 0.22 - 0.13)


def test_report_json_is_valid(daily_bars, frames_loader, tmp_path):
    from tm_engine.lab.report import plot_equity, to_markdown, write_json

    loader = frames_loader(_frames(daily_bars))
    report = walk_forward(_trend_config(engine="nautilus"), loader=loader)
    path = write_json(report, tmp_path / "trend.json")
    data = json.loads(path.read_text())
    assert len(data["windows"]) == 4
    assert data["equity_daily"]["strategy"][0] == ["2021-01-01", 100000.0]
    assert "strategy_BTCUSDT" in data["equity_daily"]
    assert data["execution"]["engine"] == "nautilus"
    md = to_markdown(report, image="trend.png")
    for text in ("Veredicto", "Por ventana", "Monte Carlo", "Nautilus", "trend.png", "2021-T4"):
        assert text in md
    png = plot_equity(report, tmp_path / "img" / "trend.png")
    assert png.read_bytes()[:4] == b"\x89PNG"
