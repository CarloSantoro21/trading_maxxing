"""CLI: `python -m tm_marketdata download|report`."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import date

from tm_marketdata.quality import series_report
from tm_marketdata.storage import ParquetStore, default_data_dir
from tm_marketdata.timeframes import TIMEFRAMES
from tm_marketdata.vision import VisionDownloader, YearResult

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT"]


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("inalcanzable")  # pragma: no cover


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tm_marketdata", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
        p.add_argument("--tf", nargs="+", default=list(TIMEFRAMES), choices=list(TIMEFRAMES))
        p.add_argument(
            "--data-dir",
            default=None,
            help="raíz de los Parquet (por defecto $TM_DATA_DIR o data/bars)",
        )

    download = sub.add_parser("download", help="descarga histórica de data.binance.vision")
    common(download)
    download.add_argument("--start", type=date.fromisoformat, default=date(2020, 1, 1))
    download.add_argument("--end", type=date.fromisoformat, default=date(2025, 12, 31))
    download.add_argument("--workers", type=int, default=4)

    report = sub.add_parser("report", help="velas, tamaño y huecos de lo que hay en disco")
    common(report)
    report.add_argument("--max-gaps", type=int, default=5, help="huecos a listar por serie")
    return parser


def _print_download(results: list[YearResult]) -> int:
    downloaded = sum(len(r.downloaded) for r in results)
    skipped = sum(len(r.skipped) for r in results)
    print(f"meses descargados: {downloaded}, ya existentes: {skipped}")
    for r in results:
        if r.missing:
            print(f"  sin publicar: {r.symbol} {r.tf} {r.year} meses {r.missing}")
        if r.unverified:
            print(f"  sin .CHECKSUM: {r.symbol} {r.tf} {r.year} meses {r.unverified}")
        for error in r.errors:
            print(f"  ERROR {r.symbol} {r.tf} {error}")
    return 1 if any(r.errors for r in results) else 0


def _print_report(store: ParquetStore, symbols: Sequence[str], tfs: Sequence[str], max_gaps: int):
    total = 0
    for symbol in symbols:
        for tf in tfs:
            rep = series_report(store, symbol, tf)
            total += rep.size_bytes
            if rep.rows == 0:
                print(f"{symbol} {tf}: sin datos")
                continue
            print(
                f"{symbol} {tf}: {rep.rows:,} velas, {rep.first:%Y-%m-%d %H:%M} a "
                f"{rep.last:%Y-%m-%d %H:%M}, {_human_size(rep.size_bytes)}, "
                f"{len(rep.gaps)} huecos ({rep.missing_bars:,} velas faltantes)"
            )
            for gap in sorted(rep.gaps, key=lambda g: g.missing, reverse=True)[:max_gaps]:
                print(f"    {gap.start:%Y-%m-%d %H:%M} -> {gap.end:%Y-%m-%d %H:%M} ({gap.missing})")
    print(f"tamaño total: {_human_size(total)}")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    store = ParquetStore(args.data_dir or default_data_dir())
    symbols = [s.replace("/", "").upper() for s in args.symbols]
    if args.command == "download":
        downloader = VisionDownloader(store, workers=args.workers)
        code = _print_download(downloader.download(symbols, args.tf, args.start, args.end))
        _print_report(store, symbols, args.tf, max_gaps=5)
        return code
    _print_report(store, symbols, args.tf, args.max_gaps)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
