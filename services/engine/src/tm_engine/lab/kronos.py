"""Laboratorio del sleeve Kronos: umbrales en jul-dic 2024 y holdout 2025.

Kronos vio datos hasta junio de 2024 en su preentrenamiento, así que no se evalúa antes. No se
reentrena: con los pronósticos precalculados (`services/forecast/scripts/precompute.py`,
`data/forecasts/{SYMBOL}/4h/{YYYY-MM-DD}.parquet`) solo se elige `min_return`, el retorno
mediano mínimo de las trayectorias para estar en largo, con el Sharpe de jul-dic 2024. En cada
cierre de 4 h la señal es la del `KronosAgent`: largo si la mediana de `close` de la última
vela pronosticada / último cierre - 1 supera `min_return`; si no, USDT.

El holdout 2025 solo se lee con `include_holdout=True`: ahí se corre una vez el umbral elegido,
con Monte Carlo y criterios de aprobación. Sin la bandera no se abre ningún archivo de 2025.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from tm_agents.montecarlo import ApprovalCriteria, bootstrap, evaluate
from tm_marketdata.resample import resample_ohlcv
from tm_marketdata.storage import ParquetStore

from tm_engine.lab.config import HoldoutError, utc
from tm_engine.lab.execution import run_nautilus
from tm_engine.lab.metrics import daily_returns, perf_stats
from tm_engine.lab.simulator import (
    SleeveRun,
    buy_and_hold,
    daily_equity,
    simulate_sleeve,
    slice_bars,
)

H4 = pd.Timedelta(hours=4)
DEFAULT_THRESHOLDS = (0.0, 0.0025, 0.005, 0.01, 0.02)
ForecastLoader = Callable[[str, pd.Timestamp, pd.Timestamp], pd.DataFrame]
BarLoader = Callable[[str, pd.Timestamp, pd.Timestamp], pd.DataFrame]


@dataclass(frozen=True)
class KronosLabConfig:
    symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")
    data_dir: str | None = None
    forecasts_dir: str = "data/forecasts"
    is_start: str = "2024-07-01"
    is_end: str = "2025-01-01"
    holdout_start: str = "2025-01-01"
    holdout_end: str = "2026-01-01"
    include_holdout: bool = False
    thresholds: Sequence[float] = DEFAULT_THRESHOLDS
    capital: float = 100_000.0
    fee: float = 0.001
    max_drawdown: float = 0.20
    n_simulations: int = 10_000
    block_size: int = 20
    seed: int = 42
    engine: str = "vector"
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def data_end(self) -> pd.Timestamp:
        return utc(self.holdout_end) if self.include_holdout else utc(self.holdout_start)


def forecast_files(root: str | Path, symbol: str, start: pd.Timestamp, end: pd.Timestamp):
    """Archivos diarios `{root}/{symbol}/4h/YYYY-MM-DD.parquet` con día en `[start, end)`."""
    out = []
    for path in sorted((Path(root) / symbol / "4h").glob("*.parquet")):
        try:
            day = pd.Timestamp(path.stem, tz="UTC")
        except ValueError:
            continue
        if start <= day < end:
            out.append(path)
    return out


def dir_forecast_loader(root: str | Path) -> ForecastLoader:
    def load(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        files = forecast_files(root, symbol, start, end)
        if not files:
            raise FileNotFoundError(
                f"no hay pronósticos de {symbol} entre {start.date()} y {end.date()} en {root}"
            )
        return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)

    return load


def store_bar_loader(data_dir: str | None) -> BarLoader:
    store = ParquetStore(data_dir)

    def load(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        src = store.read(symbol, "15m", start, end)
        return resample_ohlcv(src[["open", "high", "low", "close", "volume"]], "4h", "15min")

    return load


def median_returns(forecasts: pd.DataFrame) -> pd.Series:
    """Mediana por `decision_time` de `close` de la última vela / `last_close` - 1."""
    last_step = forecasts["step"] == forecasts.groupby("decision_time")["step"].transform("max")
    last = forecasts.loc[last_step]
    ret = last["close"] / last["last_close"] - 1.0
    return ret.groupby(last["decision_time"]).median().sort_index()


def kronos_targets(bars: pd.DataFrame, medians: pd.Series, min_return: float) -> pd.Series:
    """Objetivo por vela de 4 h (índice = apertura): la decisión al cierre de esa vela."""
    decided = (medians > min_return).astype(float)
    decided.index = pd.DatetimeIndex(decided.index) - H4  # cierre -> apertura de la vela
    return decided.reindex(bars.index).fillna(0.0)


def _run(
    frames: Mapping[str, pd.DataFrame],
    medians: Mapping[str, pd.Series],
    min_return: float,
    start: pd.Timestamp,
    end: pd.Timestamp,
    capital: float,
    fee: float,
) -> list[SleeveRun]:
    share = capital / len(frames)
    runs = []
    for symbol, frame in frames.items():
        bars = slice_bars(frame, start, end, H4)
        target = kronos_targets(bars, medians[symbol], min_return)
        runs.append(
            simulate_sleeve(
                bars, target, capital=share, fee=fee, period=H4, sleeve="kronos", symbol=symbol
            )
        )
    return runs


def _period_report(
    runs: list[SleeveRun], hold: list[SleeveRun], start: pd.Timestamp, end: pd.Timestamp
) -> dict[str, Any]:
    eq = daily_equity(runs, start, end)
    trades = [t for r in runs for t in r.trades]
    return {
        "strategy": perf_stats(eq).to_dict(),
        "buy_and_hold": perf_stats(daily_equity(hold, start, end)).to_dict(),
        "trades": len(trades),
        "commissions": float(sum(r.commissions for r in runs)),
        "equity_daily": [[t.date().isoformat(), round(float(v), 4)] for t, v in eq.items()],
    }


def kronos_lab(
    config: KronosLabConfig,
    *,
    forecast_loader: ForecastLoader | None = None,
    bar_loader: BarLoader | None = None,
) -> dict[str, Any]:
    """Elige `min_return` en jul-dic 2024; con `include_holdout` además corre 2025."""
    is_start, is_end = utc(config.is_start), utc(config.is_end)
    if is_end > utc(config.holdout_start):
        raise HoldoutError("el periodo de ajuste de Kronos no puede cruzar el holdout")
    end = config.data_end
    forecast_loader = forecast_loader or dir_forecast_loader(config.forecasts_dir)
    bar_loader = bar_loader or store_bar_loader(config.data_dir)

    frames, medians = {}, {}
    for symbol in config.symbols:
        fc = forecast_loader(symbol, is_start, end)
        fc = fc[pd.DatetimeIndex(fc["decision_time"]) <= end]
        medians[symbol] = median_returns(fc)
        bars = bar_loader(symbol, is_start - H4, end)
        frames[symbol] = bars.loc[pd.DatetimeIndex(bars.index) < end]
    coverage = {
        s: {
            "decisions": len(m),
            "first": m.index[0].isoformat() if len(m) else None,
            "last": m.index[-1].isoformat() if len(m) else None,
        }
        for s, m in medians.items()
    }

    hold_is = buy_and_hold(
        frames, capital=config.capital, fee=config.fee, period=H4, start=is_start, end=is_end
    )
    scores = {}
    for u in config.thresholds:
        runs = _run(frames, medians, u, is_start, is_end, config.capital, config.fee)
        stats = perf_stats(daily_equity(runs, is_start, is_end))
        scores[f"{u:.4f}"] = {
            "sharpe": stats.sharpe,
            "total_return": stats.total_return,
            "max_drawdown": stats.max_drawdown,
            "trades": sum(len(r.trades) for r in runs),
        }
    best = max(
        config.thresholds,
        key=lambda u: (
            s if (s := scores[f"{u:.4f}"]["sharpe"]) is not None and np.isfinite(s) else -np.inf
        ),
    )
    best_runs = _run(frames, medians, best, is_start, is_end, config.capital, config.fee)
    result: dict[str, Any] = {
        "config": {
            "symbols": list(config.symbols),
            "is_start": config.is_start,
            "is_end": config.is_end,
            "include_holdout": config.include_holdout,
            "thresholds": list(config.thresholds),
            "fee": config.fee,
            "capital": config.capital,
        },
        "coverage": coverage,
        "chosen": {"min_return": best},
        "grid": scores,
        "in_sample": _period_report(best_runs, hold_is, is_start, is_end),
        "holdout": None,
    }
    if not config.include_holdout:
        return result

    h0, h1 = utc(config.holdout_start), utc(config.holdout_end)
    runs = _run(frames, medians, best, h0, h1, config.capital, config.fee)
    if config.engine == "nautilus":
        nt = run_nautilus(
            {s: slice_bars(f, h0, h1, H4) for s, f in frames.items()},
            [o for r in runs for o in r.orders],
            capital_by_sleeve={(r.sleeve, r.symbol): r.capital for r in runs},
            timeframe="4h",
            period=H4,
            fee=config.fee,
        )
        runs = nt.runs
    hold = buy_and_hold(frames, capital=config.capital, fee=config.fee, period=H4, start=h0, end=h1)
    report = _period_report(runs, hold, h0, h1)
    rets = daily_returns(daily_equity(runs, h0, h1)).to_numpy()
    mc = bootstrap(
        rets, config.n_simulations, seed=config.seed, block_size=min(config.block_size, len(rets))
    )
    sharpe = report["strategy"]["sharpe"]
    verdict = evaluate(
        mc,
        sharpe if sharpe is not None else float("nan"),
        report["trades"],
        ApprovalCriteria(max_drawdown_p95=config.max_drawdown),
        oos_years=report["strategy"]["days"] / 365.25,
    )
    report["montecarlo"] = {
        "max_drawdown_p50": mc.max_drawdown.p50,
        "max_drawdown_p95": mc.max_drawdown.p95,
        "max_drawdown_p99": mc.max_drawdown.p99,
    }
    report["approval"] = {"approved": verdict.approved, "reasons": list(verdict.reasons)}
    result["holdout"] = report
    return result
