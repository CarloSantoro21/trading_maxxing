"""Chequeo rápido (orden de magnitud) de las candidatas de `investigacion-nuevas-estrategias.md`.

No es el backtest formal del laboratorio (#13): retornos diarios simples, posición decidida al
cierre de la vela t y mantenida durante la vela t+1, comisión de 0.1 % por lado sobre el cambio
de exposición, sin deslizamiento ni walk-forward.

Uso (desde la raíz del repo):
    uv run python docs/estrategias/chequeo_nuevas_estrategias.py data/bars [funding.csv]

`funding.csv` es opcional: columnas symbol, calc_time (ms), interval_hours, rate, armado con
los zips mensuales públicos de
https://data.binance.vision/data/futures/um/monthly/fundingRate/<SYMBOL>/.
"""

from __future__ import annotations

import glob
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from tm_agents.strategies.trend import DEFAULT_LOOKBACKS, donchian_states

FEE = 0.001  # por lado
DAYS = 365


def load_daily(root: Path, symbol: str) -> pd.DataFrame:
    files = sorted(glob.glob(str(root / symbol / "1d" / "*.parquet")))
    return pd.concat(pd.read_parquet(f) for f in files).sort_index()


def trend_exposure(bars: pd.DataFrame) -> pd.Series:
    """Fracción de los 4 canales Donchian en largo (0, 0.25, ..., 1), como el TrendAgent."""
    h, lo, c = (bars[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    states = [donchian_states(h, lo, c, n, allow_short=False) for n in DEFAULT_LOOKBACKS]
    return pd.Series(np.mean(states, axis=0), index=bars.index)


def run(exposure: pd.Series, ret: pd.Series) -> pd.DataFrame:
    """Retorno diario bruto y neto de una exposición decidida al cierre (se aplica al día t+1)."""
    pos = exposure.shift(1).fillna(0.0)
    turnover = pos.diff().abs().fillna(pos.abs())
    gross = pos * ret
    return pd.DataFrame({"pos": pos, "gross": gross, "net": gross - FEE * turnover, "to": turnover})


def stats(df: pd.DataFrame, trend_net: pd.Series) -> dict[str, float]:
    years = len(df) / DAYS
    net, gross = df["net"], df["gross"]
    eq = (1 + net).cumprod()
    # una "operación" = una ida y vuelta completa de exposición (turnover / 2)
    return {
        "ops/año": df["to"].sum() / 2 / years,
        "bruto %/año": gross.mean() * DAYS * 100,
        "neto %/año": net.mean() * DAYS * 100,
        "comis. %/año": (gross - net).mean() * DAYS * 100,
        "Sharpe neto": net.mean() / net.std(ddof=1) * np.sqrt(DAYS) if net.std() > 0 else np.nan,
        "maxDD %": (eq / eq.cummax() - 1).min() * 100,
        "exposición media": df["pos"].mean(),
        "corr trend": net.corr(trend_net),
    }


def vol_target(expo: pd.Series, ret: pd.Series, target: float, window: int, band: float):
    """Exposición del trend escalada a `target` de volatilidad anual, sin apalancamiento.

    Solo rebalancea si el cambio de exposición pasa de `band` o si el trend cambia de voto.
    """
    vol = ret.rolling(window).std(ddof=1) * np.sqrt(DAYS)
    scale = (target / vol).clip(upper=1.0).fillna(0.0)
    raw = (expo * scale).to_numpy()
    e = expo.to_numpy()
    out = np.zeros_like(raw)
    for t in range(1, len(raw)):
        prev = out[t - 1]
        if e[t] != e[t - 1] or abs(raw[t] - prev) > band:
            out[t] = raw[t]
        else:
            out[t] = prev
    return pd.Series(out, index=expo.index)


def dip_reversal(ret: pd.Series, k: float, hold: int, window: int = 30) -> pd.Series:
    """Compra al cierre si el retorno del día < -k * sigma (sigma: `window` días previos)."""
    sigma = ret.rolling(window).std(ddof=1).shift(1)
    trigger = (ret < -k * sigma).to_numpy()
    out = np.zeros(len(ret))
    left = 0
    for t in range(len(ret)):
        if trigger[t]:
            left = hold  # un nuevo disparo reinicia el reloj (no piramida)
        out[t] = 1.0 if left > 0 else 0.0
        left = max(left - 1, 0)
    return pd.Series(out, index=ret.index)


def sma_rule(close: pd.Series, n: int) -> pd.Series:
    """Detzel et al. (2021): comprado si el cierre está sobre su media móvil de `n` días."""
    return (close > close.rolling(n).mean()).astype(float)


def weekly_rebalance(signal: pd.Series, weekday: int = 6) -> pd.Series:
    """Mantiene la decisión del cierre de `weekday` (6 = domingo, UTC) durante la semana."""
    anchor = pd.DatetimeIndex(signal.index).day_of_week == weekday
    return signal.where(anchor).ffill().fillna(0.0)


def weekly_tsmom(close: pd.Series, lookback: int = 7, staggered: bool = False) -> pd.Series:
    """Liu y Tsyvinski (2021): comprado la semana siguiente si el retorno a `lookback` días > 0.

    `staggered` reparte el capital en 7 tramos, uno por día de la semana (cada tramo se
    rebalancea una vez por semana), para no depender del día de anclaje.
    """
    sig = (close / close.shift(lookback) - 1 > 0).astype(float)
    if not staggered:
        return weekly_rebalance(sig)
    return sum(weekly_rebalance(sig, d) for d in range(7)) / 7


def dual_momentum(closes: pd.DataFrame, lookback: int) -> pd.DataFrame:
    """Cada domingo: el activo con mayor retorno a `lookback` días si es > 0; si no, USDT."""
    mom = closes / closes.shift(lookback) - 1
    best = mom.fillna(-np.inf).idxmax(axis=1)
    pick = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)
    for col in closes.columns:
        pick[col] = ((best == col) & (mom[col] > 0)).astype(float)
    return pick.apply(weekly_rebalance)


def funding_daily(path: Path, symbol: str) -> pd.Series:
    f = pd.read_csv(path)
    f = f[f["symbol"] == symbol]
    ts = pd.to_datetime(f["calc_time"], unit="ms", utc=True).dt.floor("D")
    # suma de los cobros del día UTC; el último pago del día cae a las 16:00 UTC (antes del cierre)
    return f.groupby(ts)["rate"].sum()


def main() -> None:
    root = Path(sys.argv[1])
    funding_csv = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    pd.set_option("display.width", 200)
    pd.set_option("display.float_format", lambda x: f"{x:,.2f}")
    trend_runs, rets, closes = {}, {}, {}
    for symbol in ("BTCUSDT", "ETHUSDT"):
        bars = load_daily(root, symbol)
        closes[symbol] = bars["close"]
        ret = bars["close"].pct_change().fillna(0.0)
        expo = trend_exposure(bars)
        rets[symbol] = ret
        trend_runs[symbol] = run(expo, ret)
        variants: dict[str, pd.Series] = {
            "comprar y mantener": pd.Series(1.0, index=bars.index),
            "trend (base)": expo,
            "trend + vol target 40 %": vol_target(expo, ret, 0.40, 30, 0.10),
            "trend + vol target 60 %": vol_target(expo, ret, 0.60, 30, 0.10),
        }
        for n in (20, 50, 100):
            variants[f"SMA {n} d"] = sma_rule(bars["close"], n)
        variants["TSMOM semanal"] = weekly_tsmom(bars["close"])
        variants["TSMOM 7 d en 7 tramos"] = weekly_tsmom(bars["close"], 7, staggered=True)
        variants["TSMOM 28 d en 7 tramos"] = weekly_tsmom(bars["close"], 28, staggered=True)
        variants["mezcla trend + TSMOM 7 tramos"] = (expo + variants["TSMOM 7 d en 7 tramos"]) / 2
        # mezcla de sleeves: mitad del capital en cada uno (la exposición se promedia)
        variants["mezcla trend + TSMOM semanal"] = (expo + variants["TSMOM semanal"]) / 2
        variants["mezcla trend + SMA 50"] = (expo + variants["SMA 50 d"]) / 2
        for k in (2.0, 2.5):
            for hold in (3, 5, 10):
                variants[f"dip k={k} H={hold}"] = dip_reversal(ret, k, hold)
        if funding_csv is not None:
            fd = funding_daily(funding_csv, symbol).reindex(bars.index).fillna(0.0)
            f7 = fd.rolling(7).mean()
            # umbral del percentil 90 de los 365 días previos (sin look-ahead)
            q90 = f7.rolling(365, min_periods=180).quantile(0.90).shift(1)
            hot = (f7 > q90).astype(float)
            variants["trend + filtro funding p90"] = expo * (1 - hot)
            variants["trend + funding p90 a la mitad"] = expo * (1 - 0.5 * hot)
            fwd = bars["close"].shift(-30) / bars["close"] - 1
            sample = pd.DataFrame({"f7": f7, "fwd30": fwd}).loc["2021":"2025"].dropna()
            sample["quintil"] = pd.qcut(sample["f7"], 5, labels=[1, 2, 3, 4, 5])
            print(f"\n{symbol}: retorno a 30 días (%) por quintil de funding promedio de 7 días")
            g = sample.groupby("quintil", observed=True)["fwd30"]
            summary = {
                "n": g.size(),
                "media %": g.mean() * 100,
                "mediana %": g.median() * 100,
                "P(<-20 %)": g.apply(lambda s: (s < -0.2).mean() * 100),
            }
            print(pd.DataFrame(summary))
        for label, (start, end) in {
            "2021-2024": ("2021-01-01", "2024-12-31"),
            "2025": ("2025-01-01", "2025-12-31"),
        }.items():
            runs = {name: run(e, ret).loc[start:end] for name, e in variants.items()}
            base = runs["trend (base)"]["net"]
            table = pd.DataFrame({name: stats(df, base) for name, df in runs.items()}).T
            print(f"\n{symbol} {label}")
            print(table.to_string())

    # Rotación BTC/ETH/USDT contra el trend 50/50 en BTC y ETH (dos sleeves de igual capital)
    close_df, ret_df = pd.DataFrame(closes), pd.DataFrame(rets)
    trend_df = sum(trend_runs.values()) / 2
    trend_mix = trend_df["net"]
    bh_mix = ret_df.mean(axis=1)
    rows = {}
    for label, (start, end) in {"2021-2024": ("2021", "2024"), "2025": ("2025", "2025")}.items():
        rows[f"trend 50/50 {label}"] = stats(trend_df.loc[start:end], trend_mix.loc[start:end])
        bh = pd.DataFrame({"gross": bh_mix, "net": bh_mix, "pos": 1.0, "to": 0.0})
        rows[f"comprar y mantener 50/50 {label}"] = stats(
            bh.loc[start:end], trend_mix.loc[start:end]
        )
        for lb in (28, 90):
            pick = dual_momentum(close_df, lb)
            parts = {c: run(pick[c], ret_df[c]) for c in close_df.columns}
            df = sum(parts.values())  # cada pierna usa todo el capital cuando la elige
            rows[f"dual momentum {lb} d {label}"] = stats(
                df.loc[start:end], trend_mix.loc[start:end]
            )
    print("\nPortafolio BTC + ETH")
    print(pd.DataFrame(rows).T.to_string())


if __name__ == "__main__":
    main()
