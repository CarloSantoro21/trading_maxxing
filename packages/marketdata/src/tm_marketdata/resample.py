"""Remuestreo de velas OHLCV a timeframes mayores (por ejemplo, 15m → 4h).

Binance alinea las velas de 4 h a las 00:00, 04:00, … UTC; aquí los cubos se alinean a
epoch (múltiplos exactos de la regla desde 1970-01-01 UTC), que da la misma rejilla.

Reglas de agregación: `open` = primera, `high` = máximo, `low` = mínimo, `close` = última,
`volume`, `quote_volume` y `trades` = suma. Un cubo solo se emite si está **completo**:
su inicio no es anterior a la primera vela de origen y su cierre no es posterior al cierre
de la última vela de origen (ni a `now`, si se da). Así nunca sale una vela en curso
(sin look-ahead). Los huecos internos de Binance (mantenimientos) no invalidan el cubo.
"""

from __future__ import annotations

import pandas as pd

from tm_marketdata.schema import INDEX
from tm_marketdata.timeframes import TimeLike, to_utc

_AGG = {
    "open": "first",
    "high": "max",
    "low": "min",
    "close": "last",
    "volume": "sum",
    "quote_volume": "sum",
    "trades": "sum",
}


def resample_ohlcv(
    bars: pd.DataFrame,
    rule: str,
    source_period: str | pd.Timedelta,
    *,
    now: TimeLike | None = None,
) -> pd.DataFrame:
    """Agrega `bars` (índice `open_time` UTC) a velas de `rule` (p. ej. `"4h"`).

    `source_period` es la duración de cada vela de origen (`"15min"`, `"1min"`, …) y sirve
    para saber cuándo cierra la última. Devuelve el mismo esquema de columnas que recibe.
    """
    target = pd.Timedelta(rule)
    step = pd.Timedelta(source_period)
    if target < step or target % step != pd.Timedelta(0):
        raise ValueError(f"{rule} no es múltiplo de la vela de origen ({source_period})")
    columns = [c for c in bars.columns if c in _AGG]
    if bars.empty:
        return bars[columns].copy()

    idx = pd.DatetimeIndex(bars.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    src = bars[columns].set_axis(idx).sort_index()

    out = src.resample(target, label="left", closed="left", origin="epoch").agg(
        {c: _AGG[c] for c in columns}
    )
    out = out.dropna(subset=[c for c in ("open", "close") if c in out.columns])

    first_open = src.index[0]
    data_end = src.index[-1] + step
    limit = data_end if now is None else min(data_end, to_utc(now))
    keep = (out.index >= first_open) & (out.index + target <= limit)
    out = out.loc[keep]
    if "trades" in out.columns:
        out["trades"] = out["trades"].astype("int64")
    out.index = pd.DatetimeIndex(out.index, name=INDEX)
    return out
