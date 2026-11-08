"""`walk_forward(config)`: optimización in-sample, prueba out-of-sample y Monte Carlo.

Para cada tramo (12 meses in-sample + 3 out-of-sample, avanzando 3 meses):

1. Se prueba cada combinación de la cuadrícula en el in-sample con el simulador vectorizado
   y se elige la de mayor Sharpe (diario, anualizado con 365 días) del sleeve completo.
2. Esa combinación opera el tramo out-of-sample. Los tramos se concatenan en una sola
   corrida continua (las posiciones pasan de un tramo al siguiente).

La corrida out-of-sample se ejecuta en Nautilus (`run_backtest`, comisión de 0.1 % por lado)
y en el simulador; el reporte usa la de Nautilus y registra la diferencia entre las dos.
Al final se corre el Monte Carlo del drawdown (block bootstrap de los retornos diarios) y los
criterios de aprobación (`tm_agents.montecarlo.evaluate`). El criterio usa el drawdown p95 en
un horizonte de **1 año** (simulaciones de 365 retornos diarios); el del horizonte completo
del backtest se reporta solo como referencia.

Con lotes (`trend_vol`), los trades se cuentan por periodo y no por lote: los lotes de un
mismo periodo que se traslapan en el tiempo son una sola operación.

Las velas a partir de `holdout_start` (2026) no se cargan salvo con `include_holdout=True`.
"""

from __future__ import annotations

import itertools
import logging
import math
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from tm_agents.montecarlo import ONE_YEAR_DAYS, ApprovalCriteria, bootstrap, evaluate
from tm_marketdata.storage import ParquetStore

from tm_engine.lab.config import (
    RISK_LEVEL_DRAWDOWN,
    HoldoutError,
    WalkForwardConfig,
    Window,
    utc,
)
from tm_engine.lab.execution import run_nautilus
from tm_engine.lab.metrics import daily_returns, perf_stats, sharpe_ratio, window_slice
from tm_engine.lab.simulator import (
    SimOrder,
    SimTrade,
    SleeveRun,
    buy_and_hold,
    daily_equity,
    simulate_sleeve,
    slice_bars,
)
from tm_engine.lab.strategies import StrategyAdapter, get_adapter, param_key

log = logging.getLogger(__name__)

OHLCV = ["open", "high", "low", "close", "volume"]
Loader = Callable[[str, str, pd.Timestamp, pd.Timestamp], pd.DataFrame]


# --- datos ---------------------------------------------------------------------------------


def store_loader(data_dir: str | None) -> Loader:
    store = ParquetStore(data_dir)

    def load(symbol: str, tf: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        return store.read(symbol, tf, start, end)

    return load


def load_frames(config: WalkForwardConfig, tf: str, loader: Loader) -> dict[str, pd.DataFrame]:
    """Velas de cada símbolo desde `data_start` hasta `config.data_end` (sin el holdout)."""
    start, end = utc(config.data_start), config.data_end
    frames = {}
    for symbol in config.symbols:
        frame = loader(symbol, tf, start, end)
        if frame.empty:
            raise FileNotFoundError(f"no hay velas de {tf} de {symbol} entre {start} y {end}")
        frames[symbol] = guard_holdout(frame[OHLCV].sort_index(), config)
    return frames


def guard_holdout(frame: pd.DataFrame, config: WalkForwardConfig) -> pd.DataFrame:
    """Recorta lo posterior a `data_end`; si el cargador trajo velas del holdout sin la
    bandera, las descarta (el holdout nunca llega a la estrategia por accidente)."""
    idx = pd.DatetimeIndex(frame.index)
    if idx.tz is None:
        frame = frame.set_axis(idx.tz_localize("UTC"))
        idx = pd.DatetimeIndex(frame.index)
    return frame.loc[idx < config.data_end]


# --- resultado -----------------------------------------------------------------------------


@dataclass
class WindowResult:
    window: Window
    params: dict[str, Any]
    params_label: str
    is_sharpe: float | None
    is_scores: dict[str, float | None]
    oos: dict[str, Any]
    hold: dict[str, Any]
    trades: int
    commissions: float


@dataclass
class WalkForwardReport:
    config: WalkForwardConfig
    windows: list[WindowResult]
    equity: pd.Series
    hold_equity: pd.Series
    symbol_equity: dict[str, pd.Series]
    symbol_hold_equity: dict[str, pd.Series]
    aggregate: dict[str, Any]
    hold: dict[str, Any]
    per_symbol: dict[str, dict[str, Any]]
    trades: list[SimTrade]
    commissions: float
    stability: dict[str, Any]
    montecarlo: dict[str, Any]
    approval: dict[str, Any]
    execution: dict[str, Any]
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def approved(self) -> bool:
        return bool(self.approval["approved"])


# --- núcleo --------------------------------------------------------------------------------


def _sleeve_name(adapter: StrategyAdapter, k: int) -> str:
    if adapter.n_sub_sleeves == 1:
        return adapter.name
    lots = getattr(adapter, "lots", 1)
    return f"{adapter.name}_{k}" if lots == 1 else f"{adapter.name}_{k // lots}_{k % lots}"


def _group_of(sleeve: str, lots: int) -> str:
    """Sub-sleeve (periodo) al que pertenece un lote: `trend_vol_2_7` -> `trend_vol_2`."""
    return sleeve if lots == 1 else sleeve.rsplit("_", 1)[0]


def merge_lot_trades(trades: Sequence[SimTrade], lots: int) -> list[SimTrade]:
    """Une los trades de los lotes de un mismo periodo que se traslapan en el tiempo.

    Con `lots == 1` devuelve los trades tal cual. Una operación empieza cuando se compra el
    primer lote de un periodo sin posición y termina cuando se vende el último.
    """
    if lots == 1:
        return list(trades)
    groups: dict[tuple[str, str], list[SimTrade]] = {}
    for t in trades:
        groups.setdefault((_group_of(t.sleeve, lots), t.symbol), []).append(t)
    merged: list[SimTrade] = []
    for (group, symbol), items in groups.items():
        items.sort(key=lambda t: t.entry_ts)
        block: list[SimTrade] = []
        end: pd.Timestamp | None = None
        for t in items:
            if block and end is not None and t.entry_ts >= end:
                merged.append(_merge(group, symbol, block))
                block = []
            if not block:
                end = t.exit_ts
            elif end is not None:
                end = None if t.exit_ts is None else max(end, t.exit_ts)
            block.append(t)
        merged.append(_merge(group, symbol, block))
    return sorted(merged, key=lambda t: (t.entry_ts, t.symbol, t.sleeve))


def _merge(group: str, symbol: str, block: Sequence[SimTrade]) -> SimTrade:
    qty = sum(t.quantity for t in block)
    cost = sum(t.quantity * t.entry_price for t in block)
    exit_value = sum(t.quantity * t.exit_price for t in block)
    pnl = sum(t.pnl for t in block)
    is_open = any(t.exit_ts is None for t in block)
    exits = [t.exit_ts for t in block if t.exit_ts is not None]
    return SimTrade(
        sleeve=group,
        symbol=symbol,
        entry_ts=block[0].entry_ts,
        exit_ts=None if is_open else max(exits),
        quantity=qty,
        entry_price=cost / qty,
        exit_price=exit_value / qty,
        commissions=sum(t.commissions for t in block),
        pnl=pnl,
        ret=pnl / cost if cost else 0.0,
    )


def _simulate(
    adapter: StrategyAdapter,
    frames: Mapping[str, pd.DataFrame],
    targets: Mapping[str, list[pd.Series]],
    start: pd.Timestamp,
    end: pd.Timestamp,
    capital: float,
    fee: float,
) -> list[SleeveRun]:
    """Un sleeve por símbolo y sub-sleeve, con el capital repartido por igual."""
    share = capital / len(frames) / adapter.n_sub_sleeves
    runs = []
    for symbol, frame in frames.items():
        bars = slice_bars(frame, start, end, adapter.period)
        for k, target in enumerate(targets[symbol]):
            runs.append(
                simulate_sleeve(
                    bars,
                    target,
                    capital=share,
                    fee=fee,
                    period=adapter.period,
                    sleeve=_sleeve_name(adapter, k),
                    symbol=symbol,
                )
            )
    return runs


def _score(runs: list[SleeveRun], start: pd.Timestamp, end: pd.Timestamp) -> float | None:
    return sharpe_ratio(daily_returns(daily_equity(runs, start, end)))


def _count_trades(trades: Sequence[SimTrade], start: pd.Timestamp, end: pd.Timestamp) -> int:
    return sum(1 for t in trades if start < t.entry_ts <= end)


def _commissions(orders: Sequence[SimOrder], start: pd.Timestamp, end: pd.Timestamp) -> float:
    return float(sum(o.commission for o in orders if start < o.ts <= end))


def _stats(equity: pd.Series) -> dict[str, Any]:
    return perf_stats(equity).to_dict()


def _stability(results: Sequence[WindowResult]) -> dict[str, Any]:
    keys = [r.params_label for r in results]
    counts = Counter(keys)
    changes = sum(1 for a, b in itertools.pairwise(keys) if a != b)
    top, top_n = counts.most_common(1)[0] if counts else ("", 0)
    return {
        "distinct": len(counts),
        "changes": changes,
        "most_common": top,
        "most_common_share": top_n / len(keys) if keys else 0.0,
        "counts": dict(counts),
    }


def walk_forward(
    config: WalkForwardConfig,
    *,
    loader: Loader | None = None,
    adapter: StrategyAdapter | None = None,
) -> WalkForwardReport:
    """Corre el walk-forward completo de `config.strategy` y devuelve el reporte."""
    t0 = time.perf_counter()
    adapter = adapter or get_adapter(config.strategy)
    windows = config.windows()  # lanza HoldoutError si se cruza el holdout sin la bandera
    loader = loader or store_loader(config.data_dir)
    frames = load_frames(config, adapter.timeframe.value, loader)
    last_needed = windows[-1].oos_end
    for symbol, frame in frames.items():
        if (
            not config.include_holdout
            and len(frame)
            and frame.index[-1] >= utc(config.holdout_start)
        ):  # pragma: no cover - guard_holdout ya lo impide
            raise HoldoutError(f"{symbol}: hay velas del holdout sin la bandera")
        if frame.index[-1] + adapter.period < last_needed - pd.Timedelta(days=7):
            log.warning("%s: las velas terminan en %s", symbol, frame.index[-1])
    grid = [dict(p) for p in (config.grid or adapter.default_grid())]
    timings = {"load": time.perf_counter() - t0}

    # 1) In-sample: la cuadrícula en cada ventana (con caché de series objetivo).
    cache: dict[str, dict[str, list[pd.Series]]] = {}

    def targets_for(fitted: Mapping[str, Any]) -> dict[str, list[pd.Series]]:
        key = param_key(fitted)
        if key not in cache:
            cache[key] = {s: adapter.targets(f, fitted) for s, f in frames.items()}
        return cache[key]

    chosen: list[tuple[Window, dict[str, Any], float | None, dict[str, float | None]]] = []
    for w in windows:
        is_frames = {
            s: slice_bars(f, w.is_start, w.is_end, adapter.period) for s, f in frames.items()
        }
        scores: dict[str, float | None] = {}
        best: tuple[float, int, dict[str, Any]] | None = None
        for i, params in enumerate(grid):
            # la apertura del reloj de volumen se ajusta solo con BTC/ETH in-sample
            fitted = adapter.fit(params, next(iter(is_frames.values())))
            runs = _simulate(
                adapter, frames, targets_for(fitted), w.is_start, w.is_end, config.capital,
                config.fee,
            )  # fmt: skip
            score = _score(runs, w.is_start, w.is_end)
            scores[adapter.describe(fitted)] = score
            value = score if score is not None and math.isfinite(score) else -math.inf
            if best is None or value > best[0]:
                best = (value, i, fitted)
        assert best is not None
        chosen.append((w, best[2], None if best[0] == -math.inf else best[0], scores))
    timings["in_sample"] = time.perf_counter() - t0 - timings["load"]

    # 2) Out-of-sample: objetivos de cada tramo con sus parámetros, concatenados.
    oos_start, oos_end = windows[0].oos_start, windows[-1].oos_end
    oos_targets: dict[str, list[pd.Series]] = {}
    for symbol, frame in frames.items():
        bars = slice_bars(frame, oos_start, oos_end, adapter.period)
        pieces: list[list[pd.Series]] = [[] for _ in range(adapter.n_sub_sleeves)]
        for w, fitted, _, _ in chosen:
            for k, target in enumerate(targets_for(fitted)[symbol]):
                sub = slice_bars(target.to_frame("t"), w.oos_start, w.oos_end, adapter.period)
                pieces[k].append(sub["t"])
        oos_targets[symbol] = [
            pd.concat(p).reindex(bars.index).fillna(0.0) if p else pd.Series(0.0, index=bars.index)
            for p in pieces
        ]
    vector_runs = _simulate(
        adapter, frames, oos_targets, oos_start, oos_end, config.capital, config.fee
    )
    vector_equity = daily_equity(vector_runs, oos_start, oos_end)

    execution: dict[str, Any] = {"engine": config.engine}
    runs = vector_runs
    if config.engine == "nautilus":
        t1 = time.perf_counter()
        oos_frames = {
            s: slice_bars(f, oos_start, oos_end, adapter.period) for s, f in frames.items()
        }
        nt = run_nautilus(
            oos_frames,
            [o for r in vector_runs for o in r.orders],
            capital_by_sleeve={(r.sleeve, r.symbol): r.capital for r in vector_runs},
            timeframe=adapter.timeframe.value,
            period=adapter.period,
            fee=config.fee,
        )
        runs = nt.runs
        nt_equity = daily_equity(runs, oos_start, oos_end)
        diff = (nt_equity - vector_equity).abs() / vector_equity
        execution.update(
            {
                "nautilus_seconds": time.perf_counter() - t1,
                "fills": nt.fills,
                "rejections": nt.rejections[:20],
                "n_rejections": len(nt.rejections),
                "final_equity_nautilus": float(nt_equity.iloc[-1]),
                "final_equity_vector": float(vector_equity.iloc[-1]),
                "max_rel_diff": float(diff.max()),
                "final_rel_diff": float(diff.iloc[-1]),
            }
        )
    timings["out_of_sample"] = time.perf_counter() - t0 - sum(timings.values())

    equity = daily_equity(runs, oos_start, oos_end)
    lots = getattr(adapter, "lots", 1)
    trades = merge_lot_trades([t for r in runs for t in r.trades], lots)
    orders = [o for r in runs for o in r.orders]
    hold_runs = buy_and_hold(
        dict(frames), capital=config.capital, fee=config.fee, period=adapter.period,
        start=oos_start, end=oos_end,
    )  # fmt: skip
    hold_equity = daily_equity(hold_runs, oos_start, oos_end)

    results: list[WindowResult] = []
    for w, fitted, is_sharpe, scores in chosen:
        eq = window_slice(equity, w.oos_start, w.oos_end)
        hold = window_slice(hold_equity, w.oos_start, w.oos_end)
        results.append(
            WindowResult(
                window=w,
                params=fitted,
                params_label=adapter.describe(fitted),
                is_sharpe=is_sharpe,
                is_scores=scores,
                oos=_stats(eq),
                hold=_stats(hold),
                trades=_count_trades(trades, w.oos_start, w.oos_end),
                commissions=_commissions(orders, w.oos_start, w.oos_end),
            )
        )

    per_symbol: dict[str, dict[str, Any]] = {}
    symbol_equity: dict[str, pd.Series] = {}
    symbol_hold: dict[str, pd.Series] = {}
    for symbol in frames:
        mine = [r for r in runs if r.symbol == symbol]
        hold_mine = [r for r in hold_runs if r.symbol == symbol]
        symbol_equity[symbol] = daily_equity(mine, oos_start, oos_end)
        symbol_hold[symbol] = daily_equity(hold_mine, oos_start, oos_end)
        per_symbol[symbol] = {
            "strategy": _stats(symbol_equity[symbol]),
            "hold": _stats(symbol_hold[symbol]),
            "trades": sum(1 for t in trades if t.symbol == symbol),
            "commissions": float(sum(r.commissions for r in mine)),
        }

    aggregate = _stats(equity)
    hold_stats = _stats(hold_equity)
    n_trades = len(trades)
    commissions = float(sum(o.commission for o in orders))
    oos_years = aggregate["days"] / 365.25
    durations = [t.duration_days for t in trades if t.duration_days is not None]
    aggregate.update(
        {
            "trades": n_trades,
            "trades_per_year": n_trades / oos_years if oos_years else 0.0,
            "median_trade_days": float(np.median(durations)) if durations else None,
            "win_rate": float(np.mean([t.pnl > 0 for t in trades])) if trades else None,
            "commissions": commissions,
            "commissions_pct_capital": commissions / config.capital,
            "oos_years": oos_years,
            "exposure": _exposure(runs, frames, adapter, oos_start, oos_end),
        }
    )

    # 3) Monte Carlo del drawdown sobre los retornos diarios y criterios de aprobación. El
    # criterio usa simulaciones de un año (365 días); el horizonte completo es referencia.
    rets = daily_returns(equity).to_numpy()
    block = min(config.block_size, len(rets))
    mc = bootstrap(
        rets, config.n_simulations, seed=config.seed, block_size=block, initial_capital=1.0,
        horizon=ONE_YEAR_DAYS,
    )  # fmt: skip
    full = bootstrap(
        rets, config.n_simulations, seed=config.seed, block_size=block, initial_capital=1.0
    )
    sharpe = aggregate["sharpe"] if aggregate["sharpe"] is not None else float("nan")
    criteria = ApprovalCriteria(
        max_drawdown_p95=config.max_drawdown, drawdown_horizon=ONE_YEAR_DAYS
    )
    verdict = evaluate(mc, sharpe, n_trades, criteria, oos_years=oos_years)
    montecarlo = {
        "method": mc.method,
        "n_simulations": mc.n_simulations,
        "block_size": mc.block_size,
        "horizon_days": mc.n_trades,
        "max_drawdown": _summary(mc.max_drawdown),
        "final_return": _summary(mc.final_return),
        "probability_of_ruin": mc.probability_of_ruin,
        "ruin_level": mc.ruin_level,
        "full_horizon": {
            "horizon_days": full.n_trades,
            "max_drawdown": _summary(full.max_drawdown),
            "final_return": _summary(full.final_return),
            "probability_of_ruin": full.probability_of_ruin,
        },
    }
    approval = {
        "approved": verdict.approved,
        "criteria": {
            "min_oos_sharpe": criteria.min_oos_sharpe,
            "max_drawdown_p95": criteria.max_drawdown_p95,
            "min_trades": criteria.min_trades,
            "low_freq_min_trades": criteria.low_freq_min_trades,
            "low_freq_min_oos_years": criteria.low_freq_min_oos_years,
            "drawdown_horizon_days": criteria.drawdown_horizon,
        },
        "checks": [
            {
                "name": c.name,
                "passed": c.passed,
                "value": c.value,
                "threshold": c.threshold,
                "reason": c.reason,
            }
            for c in verdict.checks
        ],
        "reasons": list(verdict.reasons),
        "exposure_by_risk_level": exposure_by_risk_level(mc.max_drawdown.p95),
        "beats_buy_and_hold_sharpe": (
            aggregate["sharpe"] is not None
            and hold_stats["sharpe"] is not None
            and aggregate["sharpe"] > hold_stats["sharpe"]
        ),
    }
    timings["total"] = time.perf_counter() - t0
    return WalkForwardReport(
        config=config,
        windows=results,
        equity=equity,
        hold_equity=hold_equity,
        symbol_equity=symbol_equity,
        symbol_hold_equity=symbol_hold,
        aggregate=aggregate,
        hold=hold_stats,
        per_symbol=per_symbol,
        trades=trades,
        commissions=commissions,
        stability=_stability(results),
        montecarlo=montecarlo,
        approval=approval,
        execution=execution,
        timings=timings,
    )


def exposure_by_risk_level(p95: float) -> dict[int, float]:
    """Fracción del sleeve que invierte cada nivel: `min(1, drawdown del nivel / p95)`."""
    if not math.isfinite(p95) or p95 <= 0:
        return dict.fromkeys(RISK_LEVEL_DRAWDOWN, 1.0)
    return {level: min(1.0, dd / p95) for level, dd in RISK_LEVEL_DRAWDOWN.items()}


def _summary(d: Any) -> dict[str, float]:
    return {k: float(getattr(d, k)) for k in ("mean", "p5", "p50", "p95", "p99", "max")}


def _exposure(
    runs: Sequence[SleeveRun],
    frames: Mapping[str, pd.DataFrame],
    adapter: StrategyAdapter,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> float:
    """Exposición media: fracción del tiempo con posición, promediada sobre sub-sleeves (y
    lotes) y símbolos. Con lotes es la fracción media del capital invertida."""
    span = (end - start) / pd.Timedelta(days=1)
    if not runs or span <= 0:
        return 0.0
    held = 0.0
    for t in (t for r in runs for t in r.trades):
        exit_ts = t.exit_ts or end
        held += (exit_ts - t.entry_ts) / pd.Timedelta(days=1)
    return held / (span * len(runs))
