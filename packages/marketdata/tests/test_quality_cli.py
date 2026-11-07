import hashlib

import httpx
import pandas as pd
import respx
from tm_marketdata.cli import main
from tm_marketdata.quality import find_gaps, series_report
from tm_marketdata.schema import normalize_klines
from tm_marketdata.storage import ParquetStore
from tm_marketdata.vision import parse_kline_zip

from .conftest import FIXTURES, load_json

TOY_ZIP = (FIXTURES / "BTCUSDT-1d-2020-01.zip").read_bytes()


def test_find_gaps():
    df = normalize_klines(load_json("klines_btcusdt_1m_page1.json"))
    assert find_gaps(df, "1m") == []
    assert find_gaps(df.iloc[:1], "1m") == []
    gaps = find_gaps(df.drop(df.index[1]), "1m")
    assert len(gaps) == 1
    assert gaps[0].missing == 1
    assert gaps[0].start == pd.Timestamp("2026-01-01 00:01", tz="UTC")


def test_series_report(tmp_path):
    store = ParquetStore(tmp_path)
    assert series_report(store, "BTCUSDT", "1d").rows == 0
    df = parse_kline_zip(TOY_ZIP)
    store.write_year("BTCUSDT", "1d", 2020, df.drop(df.index[10:13]), {1})
    report = series_report(store, "BTCUSDT", "1d")
    assert report.rows == 28
    assert report.missing_bars == 3
    assert report.size_bytes > 0


@respx.mock
def test_cli_download_and_report(tmp_path, capsys):
    respx.get(url__regex=r".*\.zip$").mock(return_value=httpx.Response(200, content=TOY_ZIP))
    respx.get(url__regex=r".*\.CHECKSUM$").mock(
        return_value=httpx.Response(200, text=hashlib.sha256(TOY_ZIP).hexdigest() + "  x.zip")
    )
    args = ["--symbols", "BTCUSDT", "--tf", "1d", "--data-dir", str(tmp_path)]
    code = main(["download", *args, "--start", "2020-01-01", "--end", "2020-01-31"])
    out = capsys.readouterr().out
    assert code == 0
    assert "meses descargados: 1" in out
    assert "BTCUSDT 1d: 31 velas" in out
    assert (tmp_path / "BTCUSDT" / "1d" / "2020.parquet").exists()

    assert main(["report", *args]) == 0
    assert "0 huecos" in capsys.readouterr().out
    assert main(["report", "--symbols", "ETHUSDT", "--tf", "1d", "--data-dir", str(tmp_path)]) == 0
    assert "sin datos" in capsys.readouterr().out


@respx.mock
def test_cli_download_reports_missing_and_errors(tmp_path, capsys):
    respx.get(url__regex=r".*BTCUSDT-1d-2020-01\.zip$").mock(return_value=httpx.Response(404))
    respx.get(url__regex=r".*BTCUSDT-1d-2020-02\.zip$").mock(
        return_value=httpx.Response(200, content=TOY_ZIP)
    )
    respx.get(url__regex=r".*\.CHECKSUM$").mock(return_value=httpx.Response(200, text="bad  x"))
    code = main(
        [
            "download",
            "--symbols",
            "BTCUSDT",
            "--tf",
            "1d",
            "--data-dir",
            str(tmp_path),
            "--start",
            "2020-01-01",
            "--end",
            "2020-02-28",
        ]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "sin publicar" in out
    assert "ERROR" in out


def test_cli_report_gaps(tmp_path, capsys):
    store = ParquetStore(tmp_path)
    df = parse_kline_zip(TOY_ZIP)
    store.write_year("BTCUSDT", "1d", 2020, df.drop(df.index[5]), {1})
    main(["report", "--symbols", "BTCUSDT", "--tf", "1d", "--data-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert "1 huecos (1 velas faltantes)" in out
    assert "2020-01-06 00:00 -> 2020-01-07 00:00 (1)" in out
