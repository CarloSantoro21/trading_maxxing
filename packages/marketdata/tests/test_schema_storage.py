import pandas as pd
import pytest
from tm_marketdata.schema import COLUMNS, OHLCV, empty_frame, normalize_klines, to_milliseconds
from tm_marketdata.storage import ParquetStore, default_data_dir
from tm_marketdata.timeframes import normalize_symbol, tf_ms, to_ms, to_utc

from .conftest import load_json


def test_tf_ms_and_invalid():
    assert tf_ms("1m") == 60_000
    assert tf_ms("1d") == 86_400_000
    with pytest.raises(ValueError, match="no soportado"):
        tf_ms("4h")


def test_to_utc_variants():
    assert to_utc("2024-01-01").tzname() == "UTC"
    assert to_utc(pd.Timestamp("2024-01-01", tz="America/Mexico_City")).hour == 6
    assert to_ms(0) == 0
    assert to_ms("1970-01-01T00:00:01Z") == 1000


def test_normalize_symbol():
    assert normalize_symbol("btc/usdt") == "BTCUSDT"
    assert normalize_symbol("ETH-USDT") == "ETHUSDT"


def test_to_milliseconds_detects_microseconds():
    ms = 1_735_689_600_000
    assert list(to_milliseconds([ms, ms * 1000])) == [ms, ms]


def test_normalize_rest_rows():
    df = normalize_klines(load_json("klines_btcusdt_1m_page1.json"))
    assert list(df.columns) == COLUMNS
    assert str(df.index.tz) == "UTC"
    assert df.index.name == "open_time"
    assert all(df[c].dtype == "float64" for c in OHLCV)
    assert df.index[0] == pd.Timestamp("2026-01-01", tz="UTC")
    assert df["close"].iloc[0] == pytest.approx(87648.0)


def test_normalize_empty():
    assert normalize_klines([]).empty
    assert list(empty_frame().columns) == COLUMNS


def test_store_roundtrip_and_months(tmp_path):
    store = ParquetStore(tmp_path)
    df = normalize_klines(load_json("klines_btcusdt_1m_page1.json"))
    path = store.write_year("BTCUSDT", "1m", 2026, df, {1})
    assert path == tmp_path / "BTCUSDT" / "1m" / "2026.parquet"
    assert store.years("BTCUSDT", "1m") == [2026]
    assert store.months_in("BTCUSDT", "1m", 2026) == {1}
    assert store.months_in("BTCUSDT", "1m", 2025) == set()
    assert store.years("ETHUSDT", "1m") == []

    got = store.read("BTCUSDT", "1m", "2026-01-01 00:01", "2026-01-01 00:03")
    assert len(got) == 2
    assert store.read("BTCUSDT", "1m", "2026-01-02", "2026-01-01").empty
    assert store.read("BTCUSDT", "1m", "2020-01-01", "2021-01-01").empty


def test_default_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("TM_DATA_DIR", str(tmp_path))
    assert default_data_dir() == tmp_path
    assert ParquetStore().root == tmp_path
