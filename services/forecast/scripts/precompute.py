"""Precalcula los pronósticos de Kronos sobre velas de 4 h para el backtest del sleeve Kronos.

Para cada cierre de vela de 4 h en `[--start, --end]` (por defecto del 2024-07-01 al
2025-12-31, el periodo posterior al corte de preentrenamiento de Kronos) toma las últimas
`--context` velas de 4 h cerradas (remuestreadas desde 15m con `tm_marketdata`), genera
`--samples` trayectorias de `--pred-len` velas con `KronosForecastService` (en proceso, sin
pasar por HTTP) y guarda un Parquet por símbolo y día:

    data/forecasts/{SYMBOL}/4h/{YYYY-MM-DD}.parquet

Columnas (formato largo): `decision_time` (cierre de la última vela del contexto, cuando el
agente decidiría), `sample`, `step`, `timestamp` (apertura de la vela pronosticada),
`open, high, low, close, volume`, `last_close` y `context_len`. Los parámetros (modelo,
dispositivo, semilla…) van en los metadatos del archivo.

Es reanudable: un día cuyo archivo ya existe no se recalcula, y cada archivo se escribe a un
temporal y se renombra al final, así que un corte a medias no deja archivos rotos. Cada
decisión usa la misma semilla, así que el resultado no depende de en qué corrida se calculó.

Uso (desde la raíz del repo, después de `uv sync --all-packages`):

    uv run python services/forecast/scripts/precompute.py \
        --bars-dir data/bars --out-dir data/forecasts \
        --start 2024-07-01 --end 2024-07-02 --symbols BTCUSDT ETHUSDT
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from tm_forecast.service import MAX_CONTEXT, MIN_CANDLES, ForecastSettings, KronosForecastService
from tm_marketdata.resample import resample_ohlcv
from tm_marketdata.storage import ParquetStore

logger = logging.getLogger("precompute")

OHLCV = ["open", "high", "low", "close", "volume"]
H4 = pd.Timedelta(hours=4)
SOURCE_PERIODS = {"1m": "1min", "15m": "15min", "30m": "30min"}
_META_KEY = b"tm_forecast"


def load_4h(
    store: ParquetStore,
    symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    context: int,
    source_tf: str = "15m",
) -> pd.DataFrame:
    """Velas de 4 h que cubren `[start - context velas, end)`, remuestreadas de `source_tf`."""
    first = start - H4 * (context + 6)  # margen para huecos de Binance
    src = store.read(symbol, source_tf, first, end)
    if src.empty:
        raise FileNotFoundError(f"no hay velas de {source_tf} de {symbol} en {store.root}")
    return resample_ohlcv(src[OHLCV], "4h", SOURCE_PERIODS[source_tf])


def decision_times(bars_4h: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> list:
    """Cierres de vela de 4 h dentro de `[start, end)` (instantes en que se decide)."""
    closes = pd.DatetimeIndex(bars_4h.index) + H4
    return [t for t in closes if start <= t < end]


def day_path(out_dir: Path, symbol: str, day: pd.Timestamp) -> Path:
    return out_dir / symbol / "4h" / f"{day:%Y-%m-%d}.parquet"


def forecast_day(
    service: KronosForecastService,
    bars_4h: pd.DataFrame,
    times: Sequence[pd.Timestamp],
    *,
    context: int,
    pred_len: int,
    samples: int,
    seed: int | None,
) -> pd.DataFrame:
    """Pronósticos de las decisiones `times` en formato largo (una fila por vela y muestra)."""
    rows = []
    for t in times:
        hist = bars_4h[bars_4h.index + H4 <= t].tail(context)
        if len(hist) < MIN_CANDLES:
            logger.warning("%s: solo %d velas de contexto, se omite", t, len(hist))
            continue
        frame = hist[OHLCV].rename_axis("timestamp").reset_index()
        fc = service.predict(frame, pred_len=pred_len, samples=samples, seed=seed)
        n, steps, _ = fc.paths.shape
        flat = fc.paths.reshape(n * steps, len(OHLCV))
        part = pd.DataFrame(flat, columns=OHLCV)
        part.insert(0, "decision_time", t)
        part.insert(1, "sample", [s for s in range(n) for _ in range(steps)])
        part.insert(2, "step", list(range(steps)) * n)
        part.insert(3, "timestamp", list(pd.DatetimeIndex(fc.timestamps)) * n)
        part["last_close"] = float(hist["close"].iloc[-1])
        part["context_len"] = len(hist)
        rows.append(part)
    if not rows:
        return pd.DataFrame()
    out = pd.concat(rows, ignore_index=True)
    out["sample"] = out["sample"].astype("int16")
    out["step"] = out["step"].astype("int16")
    out["context_len"] = out["context_len"].astype("int16")
    return out


def write_atomic(df: pd.DataFrame, path: Path, meta: dict) -> None:
    """Escribe a `path.tmp` y renombra: un corte nunca deja un Parquet a medias."""
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(df, preserve_index=False)
    table = table.replace_schema_metadata(
        {**(table.schema.metadata or {}), _META_KEY: json.dumps(meta).encode()}
    )
    tmp = path.with_suffix(".parquet.tmp")
    pq.write_table(table, tmp)
    os.replace(tmp, path)


def read_forecasts(out_dir: str | Path, symbol: str) -> pd.DataFrame:
    """Lee todos los días precalculados de `symbol` (para el backtest del sleeve Kronos)."""
    files = sorted((Path(out_dir) / symbol / "4h").glob("*.parquet"))
    if not files:
        return pd.DataFrame()
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def run(
    service: KronosForecastService,
    store: ParquetStore,
    out_dir: Path,
    symbols: Sequence[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    context: int = 400,
    pred_len: int = 6,
    samples: int = 20,
    seed: int | None = 42,
    source_tf: str = "15m",
) -> dict[str, int]:
    """Calcula los días que falten; devuelve cuántos días nuevos se escribieron por símbolo."""
    written: dict[str, int] = {}
    for symbol in symbols:
        bars = load_4h(store, symbol, start, end, context, source_tf)
        times = decision_times(bars, start, end)
        by_day: dict[pd.Timestamp, list[pd.Timestamp]] = {}
        for t in times:  # 6 decisiones por día: 00:00, 04:00, …, 20:00 UTC
            by_day.setdefault(t.normalize(), []).append(t)
        written[symbol] = 0
        for day, day_times in sorted(by_day.items()):
            path = day_path(out_dir, symbol, day)
            if path.exists():
                logger.info("%s %s ya existe, se omite", symbol, day.date())
                continue
            t0 = time.perf_counter()
            df = forecast_day(
                service,
                bars,
                day_times,
                context=context,
                pred_len=pred_len,
                samples=samples,
                seed=seed,
            )
            if df.empty:
                continue
            meta = {
                "symbol": symbol,
                "model": service.settings.model,
                "device": service.device,
                "context": context,
                "pred_len": pred_len,
                "samples": samples,
                "seed": seed,
                "source_tf": source_tf,
            }
            write_atomic(df, path, meta)
            written[symbol] += 1
            logger.info(
                "%s %s: %d decisiones en %.1f s",
                symbol,
                day.date(),
                df["decision_time"].nunique(),
                time.perf_counter() - t0,
            )
    return written


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    p.add_argument("--start", default="2024-07-01", help="Primer día (UTC, inclusive)")
    p.add_argument("--end", default="2025-12-31", help="Último día (UTC, inclusive)")
    p.add_argument("--bars-dir", default=os.environ.get("TM_DATA_DIR", "data/bars"))
    p.add_argument("--out-dir", default="data/forecasts")
    p.add_argument("--source-tf", choices=sorted(SOURCE_PERIODS), default="15m")
    p.add_argument("--context", type=int, default=400)
    p.add_argument("--pred-len", type=int, default=6)
    p.add_argument("--samples", type=int, default=20)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--device", default=None, help="cpu, mps o cuda (default: automático)")
    args = p.parse_args(argv)
    if not MIN_CANDLES <= args.context <= MAX_CONTEXT:
        p.error(f"--context debe estar entre {MIN_CANDLES} y {MAX_CONTEXT}")
    return args


def main(argv: Sequence[str] | None = None, service: KronosForecastService | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    start = pd.Timestamp(args.start, tz="UTC")
    end = pd.Timestamp(args.end, tz="UTC") + pd.Timedelta(days=1)  # último día inclusive
    if service is None:
        settings = ForecastSettings.from_env()
        if args.device:
            settings = ForecastSettings(**{**settings.__dict__, "device": args.device})
        service = KronosForecastService(settings=settings)
    written = run(
        service,
        ParquetStore(args.bars_dir),
        Path(args.out_dir),
        args.symbols,
        start,
        end,
        context=args.context,
        pred_len=args.pred_len,
        samples=args.samples,
        seed=args.seed,
        source_tf=args.source_tf,
    )
    logger.info("días nuevos: %s", written)
    return 0


if __name__ == "__main__":
    sys.exit(main())
