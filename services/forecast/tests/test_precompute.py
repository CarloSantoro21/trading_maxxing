"""Script de precálculo de Kronos con un predictor simulado: sin red ni pesos del modelo."""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest
from tm_marketdata.storage import ParquetStore

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "precompute.py"


def load_script():
    spec = importlib.util.spec_from_file_location("tm_precompute", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["tm_precompute"] = module
    spec.loader.exec_module(module)
    return module


pre = load_script()


def write_15m(store: ParquetStore, symbol: str, start: str, end: str) -> None:
    idx = pd.date_range(start, end, freq="15min", inclusive="left", tz="UTC", name="open_time")
    close = 100 + np.cumsum(np.random.default_rng(1).normal(0, 0.1, len(idx)))
    df = pd.DataFrame(
        {
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": 1.0,
            "quote_volume": 100.0,
            "trades": np.int64(5),
        },
        index=idx,
    )
    for year in sorted(set(idx.year)):
        part = df[df.index.year == year]
        store.write_year(symbol, "15m", year, part, set(part.index.month))


@pytest.fixture
def store(tmp_path):
    s = ParquetStore(tmp_path / "bars")
    write_15m(s, "BTCUSDT", "2024-06-20", "2024-07-03")
    return s


def test_run_writes_one_file_per_day_and_resumes(tmp_path, store, service, fake_predictor):
    out = tmp_path / "forecasts"
    start, end = pd.Timestamp("2024-07-01", tz="UTC"), pd.Timestamp("2024-07-03", tz="UTC")
    kw = {"context": 40, "pred_len": 3, "samples": 4, "seed": 7}
    written = pre.run(service, store, out, ["BTCUSDT"], start, end, **kw)
    assert written == {"BTCUSDT": 2}
    files = sorted((out / "BTCUSDT" / "4h").glob("*.parquet"))
    assert [f.stem for f in files] == ["2024-07-01", "2024-07-02"]
    assert not list(out.rglob("*.tmp"))

    df = pd.read_parquet(files[0])
    assert df["decision_time"].nunique() == 6  # 00:00, 04:00, …, 20:00 UTC
    assert len(df) == 6 * 4 * 3
    assert df["context_len"].eq(40).all()
    first = df[df["decision_time"] == df["decision_time"].min()]
    assert first["decision_time"].iloc[0] == pd.Timestamp("2024-07-01", tz="UTC")
    # la primera vela pronosticada abre justo en la decisión
    assert first["timestamp"].min() == pd.Timestamp("2024-07-01", tz="UTC")
    meta = pq.read_schema(files[0]).metadata[b"tm_forecast"]
    assert b'"samples": 4' in meta

    calls = len(fake_predictor.calls)
    again = pre.run(service, store, out, ["BTCUSDT"], start, end, **kw)
    assert again == {"BTCUSDT": 0} and len(fake_predictor.calls) == calls  # reanudable

    files[1].unlink()
    assert pre.run(service, store, out, ["BTCUSDT"], start, end, **kw) == {"BTCUSDT": 1}
    allf = pre.read_forecasts(out, "BTCUSDT")
    assert allf["decision_time"].nunique() == 12


def test_context_never_includes_the_future(tmp_path, store, service):
    bars = pre.load_4h(
        store,
        "BTCUSDT",
        pd.Timestamp("2024-07-01", tz="UTC"),
        pd.Timestamp("2024-07-02", tz="UTC"),
        context=40,
    )
    t = pd.Timestamp("2024-07-01 08:00", tz="UTC")
    df = pre.forecast_day(service, bars, [t], context=40, pred_len=2, samples=2, seed=1)
    expected_last = float(bars.loc[pd.Timestamp("2024-07-01 04:00", tz="UTC"), "close"])
    assert df["last_close"].iloc[0] == pytest.approx(expected_last)


def test_skips_days_without_enough_context(tmp_path, store, service):
    early = pd.Timestamp("2024-06-20", tz="UTC")
    df = pre.forecast_day(
        service,
        pre.load_4h(store, "BTCUSDT", early, early + pd.Timedelta(days=1), context=40),
        [early + pd.Timedelta(hours=8)],
        context=40,
        pred_len=2,
        samples=2,
        seed=1,
    )
    assert df.empty
    assert pre.read_forecasts(tmp_path / "nada", "BTCUSDT").empty


def test_missing_bars_raise(tmp_path, service):
    with pytest.raises(FileNotFoundError):
        pre.load_4h(
            ParquetStore(tmp_path),
            "ETHUSDT",
            pd.Timestamp("2024-07-01", tz="UTC"),
            pd.Timestamp("2024-07-02", tz="UTC"),
            context=40,
        )


def test_main_cli(tmp_path, store, service):
    out = tmp_path / "fc"
    argv = [
        "--bars-dir",
        str(store.root),
        "--out-dir",
        str(out),
        "--symbols",
        "BTCUSDT",
        "--start",
        "2024-07-01",
        "--end",
        "2024-07-01",
        "--context",
        "40",
        "--samples",
        "2",
        "--pred-len",
        "2",
    ]
    assert pre.main(argv, service=service) == 0
    assert (out / "BTCUSDT" / "4h" / "2024-07-01.parquet").exists()
    with pytest.raises(SystemExit):
        pre.parse_args(["--context", "5"])
