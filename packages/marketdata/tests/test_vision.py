import hashlib
from datetime import date

import httpx
import pandas as pd
import pytest
import respx
from tm_marketdata.storage import ParquetStore
from tm_marketdata.vision import (
    ChecksumError,
    VisionDownloader,
    monthly_url,
    months_between,
    parse_kline_zip,
    verify_checksum,
)

from .conftest import FIXTURES, kline_csv, make_zip

TOY_ZIP = (FIXTURES / "BTCUSDT-1d-2020-01.zip").read_bytes()
TOY_CHECKSUM = (FIXTURES / "BTCUSDT-1d-2020-01.zip.CHECKSUM").read_text()
DAY_MS = 86_400_000


def checksum_for(content: bytes, name: str = "x.zip") -> str:
    return f"{hashlib.sha256(content).hexdigest()}  {name}"


def downloader(tmp_path, workers=1):
    return VisionDownloader(ParquetStore(tmp_path), sleep=lambda _: None, workers=workers)


def test_monthly_url():
    assert monthly_url("BTCUSDT", "1m", 2020, 3) == (
        "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2020-03.zip"
    )


def test_months_between():
    assert months_between(date(2020, 11, 15), date(2021, 2, 1)) == [
        (2020, 11),
        (2020, 12),
        (2021, 1),
        (2021, 2),
    ]


def test_verify_checksum():
    verify_checksum(TOY_ZIP, TOY_CHECKSUM)
    with pytest.raises(ChecksumError):
        verify_checksum(b"otro contenido", TOY_CHECKSUM)


def test_parse_toy_zip_milliseconds():
    df = parse_kline_zip(TOY_ZIP)
    assert len(df) == 31
    assert df.index[0] == pd.Timestamp("2020-01-01", tz="UTC")
    assert df["open"].iloc[0] == pytest.approx(7195.24)


def test_parse_zip_microseconds_and_header():
    start_us = int(pd.Timestamp("2025-01-01", tz="UTC").value // 1_000)
    times = [start_us + i * 60_000_000 for i in range(3)]
    df = parse_kline_zip(make_zip(kline_csv(times, 60_000_000, header=True)))
    assert list(df.index) == list(pd.date_range("2025-01-01", periods=3, freq="1min", tz="UTC"))


def test_parse_zip_errors():
    with pytest.raises(ValueError, match="CSV"):
        parse_kline_zip(make_zip("x", name="readme.txt"))
    assert parse_kline_zip(make_zip("")).empty


@respx.mock
def test_download_year_writes_and_resumes(tmp_path):
    jan = monthly_url("BTCUSDT", "1d", 2020, 1)
    feb = monthly_url("BTCUSDT", "1d", 2020, 2)
    feb_start = int(pd.Timestamp("2020-02-01", tz="UTC").value // 1_000_000)
    feb_zip = make_zip(kline_csv([feb_start + i * DAY_MS for i in range(29)], DAY_MS))
    respx.get(jan).mock(return_value=httpx.Response(200, content=TOY_ZIP))
    respx.get(jan + ".CHECKSUM").mock(return_value=httpx.Response(200, text=TOY_CHECKSUM))
    respx.get(feb).mock(return_value=httpx.Response(200, content=feb_zip))
    respx.get(feb + ".CHECKSUM").mock(return_value=httpx.Response(404))
    march = respx.get(monthly_url("BTCUSDT", "1d", 2020, 3)).mock(return_value=httpx.Response(404))

    dl = downloader(tmp_path)
    result = dl.download_year("BTCUSDT", "1d", 2020, [1, 2, 3])
    assert result.downloaded == [1, 2]
    assert result.missing == [3]
    assert result.unverified == [2]
    assert result.rows == 60
    store = ParquetStore(tmp_path)
    assert store.months_in("BTCUSDT", "1d", 2020) == {1, 2}
    assert not list(store.parts_dir("BTCUSDT", "1d").iterdir())

    # Segunda corrida: enero y febrero se saltan; solo se vuelve a intentar marzo.
    calls_before = respx.calls.call_count
    again = dl.download_year("BTCUSDT", "1d", 2020, [1, 2, 3])
    assert again.skipped == [1, 2]
    assert respx.calls.call_count == calls_before + 1
    assert march.call_count == 2

    # Si ya está todo, no se hace ninguna petición.
    assert dl.download_year("BTCUSDT", "1d", 2020, [1, 2]).skipped == [1, 2]
    assert respx.calls.call_count == calls_before + 1


@respx.mock
def test_download_year_uses_pending_parts(tmp_path):
    dl = downloader(tmp_path)
    parts = dl.store.parts_dir("BTCUSDT", "1d")
    parts.mkdir(parents=True)
    parse_kline_zip(TOY_ZIP).to_parquet(parts / "2020-01.parquet")
    result = dl.download_year("BTCUSDT", "1d", 2020, [1])
    assert result.downloaded == [1]
    assert result.rows == 31
    assert respx.calls.call_count == 0


@respx.mock
def test_download_year_reports_checksum_error(tmp_path):
    jan = monthly_url("BTCUSDT", "1d", 2020, 1)
    respx.get(jan).mock(return_value=httpx.Response(200, content=TOY_ZIP))
    respx.get(jan + ".CHECKSUM").mock(return_value=httpx.Response(200, text=checksum_for(b"otro")))
    result = downloader(tmp_path).download_year("BTCUSDT", "1d", 2020, [1])
    assert result.downloaded == []
    assert "checksum" in result.errors[0]
    assert not ParquetStore(tmp_path).year_path("BTCUSDT", "1d", 2020).exists()


@respx.mock
def test_download_year_reports_http_error(tmp_path):
    respx.get(monthly_url("BTCUSDT", "1d", 2020, 1)).mock(return_value=httpx.Response(403))
    result = downloader(tmp_path).download_year("BTCUSDT", "1d", 2020, [1])
    assert "403" in result.errors[0]


@respx.mock
def test_download_many_series(tmp_path):
    respx.get(url__regex=r".*\.zip$").mock(return_value=httpx.Response(200, content=TOY_ZIP))
    respx.get(url__regex=r".*\.CHECKSUM$").mock(
        return_value=httpx.Response(200, text=checksum_for(TOY_ZIP))
    )
    results = downloader(tmp_path, workers=3).download(
        ["btc/usdt", "ETHUSDT"], ["1d"], date(2020, 1, 1), date(2020, 1, 31)
    )
    assert sorted((r.symbol, r.year) for r in results) == [("BTCUSDT", 2020), ("ETHUSDT", 2020)]
    assert all(r.rows == 31 for r in results)
