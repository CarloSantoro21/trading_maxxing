"""Almacén local de velas en Parquet: `{root}/{symbol}/{tf}/{year}.parquet`."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from tm_marketdata.schema import INDEX, empty_frame, finalize
from tm_marketdata.timeframes import TimeLike, to_utc

DEFAULT_DATA_DIR = Path("data/bars")
# Llave de los metadatos del archivo donde se anotan los meses que contiene.
_META_KEY = b"tm_marketdata"


def default_data_dir() -> Path:
    """`TM_DATA_DIR` si está definida; si no, `data/bars` relativo al directorio actual."""
    return Path(os.environ.get("TM_DATA_DIR", DEFAULT_DATA_DIR))


class ParquetStore:
    """Lee y escribe un archivo Parquet por símbolo, timeframe y año."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else default_data_dir()

    def series_dir(self, symbol: str, tf: str) -> Path:
        return self.root / symbol / tf

    def year_path(self, symbol: str, tf: str, year: int) -> Path:
        return self.series_dir(symbol, tf) / f"{year}.parquet"

    def parts_dir(self, symbol: str, tf: str) -> Path:
        """Meses descargados que todavía no se consolidan en su archivo anual."""
        return self.series_dir(symbol, tf) / ".parts"

    def years(self, symbol: str, tf: str) -> list[int]:
        directory = self.series_dir(symbol, tf)
        if not directory.is_dir():
            return []
        return sorted(int(p.stem) for p in directory.glob("*.parquet") if p.stem.isdigit())

    def months_in(self, symbol: str, tf: str, year: int) -> set[int]:
        """Meses que ya están completos en el archivo anual (vacío si no existe)."""
        path = self.year_path(symbol, tf, year)
        if not path.exists():
            return set()
        metadata = pq.read_schema(path).metadata or {}
        info = json.loads(metadata.get(_META_KEY, b"{}"))
        return set(info.get("months", []))

    def read_year(self, symbol: str, tf: str, year: int) -> pd.DataFrame:
        path = self.year_path(symbol, tf, year)
        if not path.exists():
            return empty_frame()
        return finalize(pd.read_parquet(path, engine="pyarrow"))

    def read(self, symbol: str, tf: str, start: TimeLike, end: TimeLike) -> pd.DataFrame:
        """Velas locales con `start <= open_time < end`; vacío si no hay archivos."""
        start_ts, end_ts = to_utc(start), to_utc(end)
        if end_ts <= start_ts:
            return empty_frame()
        wanted = range(start_ts.year, end_ts.year + 1)
        frames = [self.read_year(symbol, tf, y) for y in self.years(symbol, tf) if y in wanted]
        frames = [f for f in frames if not f.empty]
        if not frames:
            return empty_frame()
        df = finalize(pd.concat(frames))
        return df[(df.index >= start_ts) & (df.index < end_ts)]

    def write_year(
        self, symbol: str, tf: str, year: int, df: pd.DataFrame, months: set[int] | list[int]
    ) -> Path:
        """Escribe el archivo anual de forma atómica, con los meses que contiene."""
        path = self.year_path(symbol, tf, year)
        path.parent.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pandas(finalize(df), preserve_index=True)
        info = json.dumps({"symbol": symbol, "tf": tf, "year": year, "months": sorted(months)})
        metadata = {**(table.schema.metadata or {}), _META_KEY: info.encode()}
        table = table.replace_schema_metadata(metadata)
        tmp = path.with_suffix(".parquet.tmp")
        pq.write_table(table, tmp, compression="zstd")
        os.replace(tmp, path)
        return path


__all__ = ["DEFAULT_DATA_DIR", "INDEX", "ParquetStore", "default_data_dir"]
