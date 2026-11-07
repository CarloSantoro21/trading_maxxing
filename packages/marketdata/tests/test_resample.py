import numpy as np
import pandas as pd
import pytest
from tm_marketdata import resample_ohlcv


def bars(n, start="2025-01-01 00:00", freq="15min", extra=True):
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC", name="open_time")
    close = np.arange(1, n + 1, dtype=float)
    df = pd.DataFrame(
        {
            "open": close - 0.5,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": np.ones(n),
        },
        index=idx,
    )
    if extra:
        df["quote_volume"] = 2.0
        df["trades"] = np.int64(3)
    return df


def test_aggregates_ohlcv_by_four_hours():
    out = resample_ohlcv(bars(32), "4h", "15min")  # 8 h exactas -> 2 velas
    assert list(out.index) == [
        pd.Timestamp("2025-01-01 00:00", tz="UTC"),
        pd.Timestamp("2025-01-01 04:00", tz="UTC"),
    ]
    first = out.iloc[0]
    assert first["open"] == 0.5 and first["close"] == 16.0
    assert first["high"] == 17.0 and first["low"] == 0.0
    assert first["volume"] == 16.0 and first["quote_volume"] == 32.0
    assert first["trades"] == 48 and out["trades"].dtype == np.int64
    assert out.index.name == "open_time"


def test_drops_incomplete_buckets_at_both_ends():
    # empieza a las 01:00 y termina a las 09:45 (cierra 10:00): solo 04:00-08:00 está completo
    out = resample_ohlcv(bars(36, start="2025-01-01 01:00", extra=False), "4h", "15min")
    assert list(out.index) == [pd.Timestamp("2025-01-01 04:00", tz="UTC")]
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]


def test_now_cuts_bucket_in_progress():
    out = resample_ohlcv(bars(32), "4h", "15min", now="2025-01-01 07:59")
    assert len(out) == 1
    out = resample_ohlcv(bars(32), "4h", "15min", now="2025-01-01 08:00")
    assert len(out) == 2


def test_internal_gap_keeps_bucket():
    df = bars(32).drop(pd.Timestamp("2025-01-01 01:00", tz="UTC"))
    out = resample_ohlcv(df, "4h", "15min")
    assert len(out) == 2 and out.iloc[0]["volume"] == 15.0


def test_naive_index_from_1m_and_empty():
    df = bars(480, freq="1min")
    df.index = df.index.tz_localize(None)
    out = resample_ohlcv(df, "4h", "1min")
    assert len(out) == 2 and str(out.index.tz) == "UTC"
    assert resample_ohlcv(df.iloc[:0], "4h", "1min").empty


def test_rejects_non_multiple():
    with pytest.raises(ValueError, match="múltiplo"):
        resample_ohlcv(bars(4), "10min", "15min")
