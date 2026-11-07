"""Revisión de calidad de las series: velas, rango y huecos."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from tm_marketdata.schema import finalize
from tm_marketdata.storage import ParquetStore
from tm_marketdata.timeframes import tf_ms


@dataclass(frozen=True)
class Gap:
    """Velas faltantes entre dos velas consecutivas presentes."""

    start: pd.Timestamp  # primera apertura que falta
    end: pd.Timestamp  # siguiente apertura presente
    missing: int


@dataclass
class SeriesReport:
    symbol: str
    tf: str
    rows: int
    first: pd.Timestamp | None
    last: pd.Timestamp | None
    size_bytes: int
    gaps: list[Gap] = field(default_factory=list)

    @property
    def missing_bars(self) -> int:
        return sum(g.missing for g in self.gaps)


def find_gaps(df: pd.DataFrame, tf: str) -> list[Gap]:
    """Huecos donde la diferencia entre aperturas consecutivas es mayor a una vela."""
    if len(df) < 2:
        return []
    step = tf_ms(tf)
    opens = df.index.asi8 // 1_000_000
    diffs = np.diff(opens)
    gaps = []
    for i in np.flatnonzero(diffs > step):
        gaps.append(
            Gap(
                start=pd.Timestamp(opens[i] + step, unit="ms", tz="UTC"),
                end=pd.Timestamp(opens[i + 1], unit="ms", tz="UTC"),
                missing=int(diffs[i] // step - 1),
            )
        )
    return gaps


def series_report(store: ParquetStore, symbol: str, tf: str) -> SeriesReport:
    years = store.years(symbol, tf)
    frames = [store.read_year(symbol, tf, y) for y in years]
    frames = [f for f in frames if not f.empty]
    df = finalize(pd.concat(frames)) if frames else None
    size = sum(store.year_path(symbol, tf, y).stat().st_size for y in years)
    if df is None:
        return SeriesReport(symbol, tf, 0, None, None, size)
    return SeriesReport(symbol, tf, len(df), df.index[0], df.index[-1], size, find_gaps(df, tf))
