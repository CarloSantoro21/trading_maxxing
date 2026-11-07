"""Descarga histórica desde los archivos mensuales de data.binance.vision."""

from __future__ import annotations

import hashlib
import io
import logging
import time
import zipfile
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date

import httpx
import pandas as pd

from tm_marketdata.http import BinanceHTTPError, default_client, get_with_retries
from tm_marketdata.schema import empty_frame, finalize, normalize_klines
from tm_marketdata.storage import ParquetStore
from tm_marketdata.timeframes import normalize_symbol, tf_ms

logger = logging.getLogger(__name__)

VISION_URL = "https://data.binance.vision"


class ChecksumError(ValueError):
    """El SHA-256 del zip no coincide con su archivo `.CHECKSUM`."""


def monthly_url(symbol: str, tf: str, year: int, month: int, base_url: str = VISION_URL) -> str:
    name = f"{symbol}-{tf}-{year}-{month:02d}.zip"
    return f"{base_url.rstrip('/')}/data/spot/monthly/klines/{symbol}/{tf}/{name}"


def verify_checksum(content: bytes, checksum_text: str) -> None:
    """El `.CHECKSUM` tiene el formato de `sha256sum`: `<hash>  <archivo>`."""
    expected = checksum_text.split()[0].strip().lower()
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected:
        raise ChecksumError(f"checksum no coincide: esperado {expected}, obtenido {actual}")


def parse_kline_zip(content: bytes) -> pd.DataFrame:
    """Lee el CSV del zip mensual. Tolera encabezado y timestamps en microsegundos."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        members = [n for n in archive.namelist() if n.lower().endswith(".csv")]
        if not members:
            raise ValueError("el zip no contiene un CSV")
        with archive.open(members[0]) as handle:
            try:
                raw = pd.read_csv(handle, header=None, dtype=str)
            except pd.errors.EmptyDataError:
                return normalize_klines([])
    if not str(raw.iat[0, 0]).strip().isdigit():  # encabezado "open_time,open,..."
        raw = raw.iloc[1:]
    return normalize_klines(raw)


def months_between(start: date, end: date) -> list[tuple[int, int]]:
    """Meses (año, mes) entre dos fechas, ambos extremos incluidos."""
    months = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


@dataclass
class YearResult:
    """Resultado de procesar un año de una serie."""

    symbol: str
    tf: str
    year: int
    downloaded: list[int] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)
    missing: list[int] = field(default_factory=list)
    unverified: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    rows: int = 0


class VisionDownloader:
    """Baja meses de data.binance.vision y los consolida en un Parquet por año.

    Es reanudable a dos niveles: los meses ya consolidados se saltan (se anotan en los
    metadatos del Parquet) y los meses bajados de un año a medias quedan en `.parts/`.
    """

    def __init__(
        self,
        store: ParquetStore,
        client: httpx.Client | None = None,
        *,
        base_url: str = VISION_URL,
        max_retries: int = 5,
        backoff: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        workers: int = 4,
    ) -> None:
        self.store = store
        self._client = client or default_client(timeout=120.0)
        self._base_url = base_url
        self._max_retries = max_retries
        self._backoff = backoff
        self._sleep = sleep
        self._workers = max(1, workers)

    def _get(self, url: str) -> httpx.Response:
        return get_with_retries(
            self._client,
            url,
            max_retries=self._max_retries,
            backoff=self._backoff,
            sleep=self._sleep,
        )

    def fetch_month(self, symbol: str, tf: str, year: int, month: int) -> tuple[pd.DataFrame, bool]:
        """Baja un mes. Devuelve (velas, verificado). Lanza FileNotFoundError si no existe."""
        url = monthly_url(symbol, tf, year, month, self._base_url)
        response = self._get(url)
        if response.status_code == 404:
            raise FileNotFoundError(url)
        if response.status_code != 200:
            raise BinanceHTTPError(f"{response.status_code} en {url}", response.status_code)
        content = response.content

        checksum = self._get(url + ".CHECKSUM")
        verified = checksum.status_code == 200
        if verified:
            verify_checksum(content, checksum.text)
        else:
            logger.warning("sin .CHECKSUM para %s (%s)", url, checksum.status_code)
        return parse_kline_zip(content), verified

    def download_year(self, symbol: str, tf: str, year: int, months: Iterable[int]) -> YearResult:
        result = YearResult(symbol, tf, year)
        have = self.store.months_in(symbol, tf, year)
        wanted = sorted(set(months))
        result.skipped = [m for m in wanted if m in have]
        todo = [m for m in wanted if m not in have]
        if not todo:
            return result

        parts_dir = self.store.parts_dir(symbol, tf)
        parts_dir.mkdir(parents=True, exist_ok=True)
        frames = [self.store.read_year(symbol, tf, year)]
        completed = set(have)
        part_paths = []
        for month in todo:
            part = parts_dir / f"{year}-{month:02d}.parquet"
            if part.exists():
                frames.append(pd.read_parquet(part, engine="pyarrow"))
            else:
                try:
                    df, verified = self.fetch_month(symbol, tf, year, month)
                except FileNotFoundError:
                    result.missing.append(month)
                    continue
                except (BinanceHTTPError, ChecksumError, ValueError, zipfile.BadZipFile) as exc:
                    result.errors.append(f"{year}-{month:02d}: {exc}")
                    logger.error("%s %s %s-%02d: %s", symbol, tf, year, month, exc)
                    continue
                if not verified:
                    result.unverified.append(month)
                df.to_parquet(part, engine="pyarrow")
                frames.append(df)
            part_paths.append(part)
            completed.add(month)
            result.downloaded.append(month)

        if result.downloaded:
            non_empty = [f for f in frames if not f.empty]
            merged = finalize(pd.concat(non_empty)) if non_empty else empty_frame()
            self.store.write_year(symbol, tf, year, merged, completed)
            result.rows = len(merged)
            for part in part_paths:
                part.unlink(missing_ok=True)
        logger.info(
            "%s %s %s: %s meses nuevos, %s ya estaban, %s sin publicar",
            symbol,
            tf,
            year,
            len(result.downloaded),
            len(result.skipped),
            len(result.missing),
        )
        return result

    def download(
        self, symbols: Iterable[str], tfs: Iterable[str], start: date, end: date
    ) -> list[YearResult]:
        """Baja todas las series en paralelo (un hilo por símbolo, timeframe y año)."""
        by_year: dict[int, list[int]] = {}
        for year, month in months_between(start, end):
            by_year.setdefault(year, []).append(month)
        tasks = []
        for symbol in symbols:
            for tf in tfs:
                tf_ms(tf)
                for year, months in by_year.items():
                    tasks.append((normalize_symbol(symbol), tf, year, months))
        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            return list(pool.map(lambda t: self.download_year(*t), tasks))
