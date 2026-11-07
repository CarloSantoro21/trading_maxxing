"""Instrumentos de Binance Spot y conversión de velas de tm_marketdata a barras de Nautilus."""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import pandas as pd
from nautilus_trader.model.currencies import BTC, ETH, USDT
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Price, Quantity
from tm_shared.contracts import Timeframe

BINANCE = Venue("BINANCE")
SPOT_FEE = Decimal("0.001")  # 0.1 % maker y taker (Binance Spot sin descuento por BNB)

# Precisiones de Binance Spot para los pares del MVP.
_SPECS: dict[str, tuple[object, int, int]] = {
    "BTCUSDT": (BTC, 2, 5),
    "ETHUSDT": (ETH, 2, 4),
}

_TF_SPEC: dict[Timeframe, str] = {
    Timeframe.M1: "1-MINUTE",
    Timeframe.M15: "15-MINUTE",
    Timeframe.M30: "30-MINUTE",
    Timeframe.D1: "1-DAY",
}

_TF_DELTA: dict[Timeframe, pd.Timedelta] = {
    Timeframe.M1: pd.Timedelta(minutes=1),
    Timeframe.M15: pd.Timedelta(minutes=15),
    Timeframe.M30: pd.Timedelta(minutes=30),
    Timeframe.D1: pd.Timedelta(days=1),
}


def instrument_id(symbol: str) -> InstrumentId:
    return InstrumentId(Symbol(symbol), BINANCE)


def spot_instrument(symbol: str, fee: Decimal = SPOT_FEE) -> CurrencyPair:
    """CurrencyPair de Binance Spot (BTCUSDT o ETHUSDT) con la comisión indicada."""
    if symbol not in _SPECS:
        raise ValueError(f"símbolo no soportado: {symbol}")
    base, price_prec, size_prec = _SPECS[symbol]
    return CurrencyPair(
        instrument_id=instrument_id(symbol),
        raw_symbol=Symbol(symbol),
        base_currency=base,
        quote_currency=USDT,
        price_precision=price_prec,
        size_precision=size_prec,
        price_increment=Price(10**-price_prec, price_prec),
        size_increment=Quantity(10**-size_prec, size_prec),
        lot_size=None,
        max_quantity=Quantity(9000, size_prec),
        min_quantity=Quantity(10**-size_prec, size_prec),
        max_notional=None,
        min_notional=None,
        max_price=Price(1_000_000, price_prec),
        min_price=Price(10**-price_prec, price_prec),
        margin_init=Decimal(0),
        margin_maint=Decimal(0),
        maker_fee=fee,
        taker_fee=fee,
        ts_event=0,
        ts_init=0,
    )


def bar_type(symbol: str, timeframe: Timeframe | str = Timeframe.M1) -> BarType:
    """Barra de último precio agregada externamente (las velas vienen de Binance)."""
    tf = Timeframe(timeframe)
    return BarType.from_str(f"{symbol}.BINANCE-{_TF_SPEC[tf]}-LAST-EXTERNAL")


def bars_from_frame(
    frame: pd.DataFrame,
    instrument: CurrencyPair,
    timeframe: Timeframe | str = Timeframe.M1,
) -> list[Bar]:
    """Convierte un DataFrame de `MarketDataService.get_bars` en barras de Nautilus.

    tm_marketdata indexa por `open_time`; Nautilus fecha cada barra a su cierre, así que se
    suma la duración del timeframe para no ver la vela antes de que termine.
    """
    tf = Timeframe(timeframe)
    index = pd.DatetimeIndex(frame.index)
    index = (index.tz_convert("UTC") if index.tz else index.tz_localize("UTC")) + _TF_DELTA[tf]
    # Se arman las barras a mano: con pandas 3 (copy-on-write) `DataFrame.values` puede ser de
    # solo lectura y el `BarDataWrangler` de Nautilus falla con frames que vienen de Parquet.
    ts = index.as_unit("ns").asi8
    columns = [np.asarray(frame[c], dtype="float64") for c in ("open", "high", "low", "close")]
    volume = np.asarray(frame["volume"], dtype="float64")
    bt = bar_type(instrument.id.symbol.value, tf)
    pp, sp = instrument.price_precision, instrument.size_precision
    return [
        Bar(
            bar_type=bt,
            open=Price(o, pp),
            high=Price(h, pp),
            low=Price(lo, pp),
            close=Price(c, pp),
            volume=Quantity(v, sp),
            ts_event=int(t),
            ts_init=int(t),
        )
        for o, h, lo, c, v, t in zip(*columns, volume, ts, strict=True)
    ]
