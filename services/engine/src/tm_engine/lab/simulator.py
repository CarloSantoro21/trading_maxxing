"""Simulador vectorizado de un sleeve spot (solo largo) con comisión por lado.

Reproduce lo que hace `AgenticStrategy` sobre el venue simulado de Nautilus, pero sin el
motor de eventos, para poder correr la cuadrícula de parámetros de 20 ventanas en segundos:

- La estrategia entrega una serie objetivo por vela (`1` = estar comprado, `0` = en USDT),
  indexada por la apertura de la vela; el objetivo de la vela `t` se ejecuta **al cierre**
  de esa vela, que es cuando la señal ya se puede calcular sin look-ahead.
- Al pasar de 0 a 1 se compra con todo el efectivo del sleeve: `qty = floor(cash / (p (1 +
  fee)))` redondeado a la precisión de Binance; al pasar de 1 a 0 se vende toda la posición.
- La comisión (`fee` por lado) se cobra en USDT sobre el nocional, como en el venue simulado.
- El equity de cada vela es efectivo + cantidad * cierre (posiciones valuadas a mercado).

Las órdenes que produce (`SimOrder`) son exactamente las que luego se mandan a Nautilus
como `TradeDecision`, así que las dos ejecuciones se pueden comparar orden por orden.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from tm_shared.contracts import OrderSide

# Decimales de cantidad de Binance Spot (iguales a `tm_engine.instruments`)
QTY_DECIMALS = {"BTCUSDT": 5, "ETHUSDT": 4}
# Margen para que el redondeo del venue (precio a 2 decimales, comisiones) nunca deje la
# compra 1 centavo arriba del saldo libre: 1 USDT por cada 100,000.
CASH_BUFFER = 1e-5


@dataclass(frozen=True)
class SimOrder:
    """Una orden a mercado al cierre de la vela que abre en `bar_open`."""

    sleeve: str
    symbol: str
    side: OrderSide
    bar_open: pd.Timestamp
    ts: pd.Timestamp  # cierre de la vela: cuándo se decide y se llena
    quantity: float
    price: float
    commission: float


@dataclass(frozen=True)
class SimTrade:
    """Una operación de ida y vuelta (o abierta al final, valuada a mercado)."""

    sleeve: str
    symbol: str
    entry_ts: pd.Timestamp
    exit_ts: pd.Timestamp | None
    quantity: float
    entry_price: float
    exit_price: float
    commissions: float
    pnl: float  # neto de comisiones, en USDT
    ret: float  # pnl / costo de entrada (incluye la comisión de compra)

    @property
    def is_open(self) -> bool:
        return self.exit_ts is None

    @property
    def duration_days(self) -> float | None:
        if self.exit_ts is None:
            return None
        return (self.exit_ts - self.entry_ts) / pd.Timedelta(days=1)


@dataclass
class SleeveRun:
    """Resultado de un sleeve: equity por vela (indexado por el cierre), órdenes y trades."""

    sleeve: str
    symbol: str
    capital: float
    equity: pd.Series
    orders: list[SimOrder] = field(default_factory=list)
    trades: list[SimTrade] = field(default_factory=list)

    @property
    def commissions(self) -> float:
        return float(sum(o.commission for o in self.orders))


def floor_qty(value: float, decimals: int) -> float:
    """Redondea hacia abajo a `decimals` (con tolerancia al error de punto flotante)."""
    scale = 10**decimals
    return math.floor(value * scale + 1e-9) / scale


def simulate_sleeve(
    bars: pd.DataFrame,
    target: pd.Series,
    *,
    capital: float,
    fee: float,
    period: pd.Timedelta,
    sleeve: str,
    symbol: str,
    qty_decimals: int | None = None,
    cash_buffer: float = CASH_BUFFER,
) -> SleeveRun:
    """Simula un sleeve que sigue `target` (0/1) sobre `bars` (índice = apertura, UTC)."""
    decimals = QTY_DECIMALS.get(symbol, 6) if qty_decimals is None else qty_decimals
    index = pd.DatetimeIndex(bars.index)
    close = bars["close"].to_numpy(dtype=float)
    tgt = target.reindex(index).fillna(0).to_numpy(dtype=float) > 0.5
    n = len(close)
    closes_at = index + period
    if n == 0:
        return SleeveRun(sleeve, symbol, capital, pd.Series(dtype=float))

    cash_path = np.empty(n)
    qty_path = np.empty(n)
    cash, qty = float(capital), 0.0
    orders: list[SimOrder] = []
    trades: list[SimTrade] = []
    entry: tuple[pd.Timestamp, float, float, float, float] | None = None  # ts, px, qty, fee, cost
    changes = np.flatnonzero(np.diff(np.concatenate([[False], tgt]).astype(np.int8)) != 0)
    last = 0
    for i in changes:
        cash_path[last:i] = cash
        qty_path[last:i] = qty
        last = i
        price = close[i]
        if tgt[i] and qty == 0:
            q = floor_qty(cash * (1.0 - cash_buffer) / (price * (1.0 + fee)), decimals)
            if q <= 0:
                continue
            commission = q * price * fee
            cash -= q * price + commission
            qty = q
            entry = (closes_at[i], price, q, commission, q * price + commission)
            orders.append(
                SimOrder(
                    sleeve, symbol, OrderSide.BUY, index[i], closes_at[i], q, price, commission
                )
            )
        elif not tgt[i] and qty > 0:
            commission = qty * price * fee
            cash += qty * price - commission
            orders.append(
                SimOrder(
                    sleeve, symbol, OrderSide.SELL, index[i], closes_at[i], qty, price, commission
                )
            )
            assert entry is not None
            ts0, px0, q0, fee0, cost0 = entry
            pnl = q0 * price - commission - cost0
            trades.append(
                SimTrade(
                    sleeve, symbol, ts0, closes_at[i], q0, px0, price, fee0 + commission, pnl,
                    pnl / cost0,
                )
            )  # fmt: skip
            qty, entry = 0.0, None
    cash_path[last:] = cash
    qty_path[last:] = qty
    if entry is not None:  # abierta al final: se valúa al último cierre
        ts0, px0, q0, fee0, cost0 = entry
        pnl = q0 * close[-1] - cost0
        trades.append(
            SimTrade(sleeve, symbol, ts0, None, q0, px0, close[-1], fee0, pnl, pnl / cost0)
        )
    equity = pd.Series(cash_path + qty_path * close, index=closes_at, name=sleeve)
    return SleeveRun(sleeve, symbol, float(capital), equity, orders, trades)


def daily_equity(runs: Sequence[SleeveRun], start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    """Equity diario combinado (suma de sleeves) a las 00:00 UTC de cada día de `[start, end]`.

    El punto del día `d` es el último equity de cada sleeve con cierre `<= d` (valuado a
    mercado); el primero es el capital inicial, porque en `start` todavía no cierra ninguna vela
    del tramo. Así el retorno del día `d` es el de las velas que cierran en `(d - 1, d]`.
    """
    days = pd.date_range(start, end, freq="1D")
    total = pd.Series(0.0, index=days)
    for run in runs:
        eq = run.equity[(run.equity.index > start) & (run.equity.index <= end)]
        daily = eq.reindex(days, method="ffill") if len(eq) else pd.Series(np.nan, index=days)
        total += daily.fillna(run.capital).to_numpy()
    return total


def buy_and_hold(
    frames: dict[str, pd.DataFrame],
    *,
    capital: float,
    fee: float,
    period: pd.Timedelta,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> list[SleeveRun]:
    """Comprar y mantener: capital repartido por igual, se compra al cierre de la primera vela."""
    runs = []
    share = capital / len(frames)
    for symbol, frame in frames.items():
        bars = slice_bars(frame, start, end, period)
        target = pd.Series(1.0, index=bars.index)
        runs.append(
            simulate_sleeve(
                bars, target, capital=share, fee=fee, period=period, sleeve="hold", symbol=symbol
            )
        )
    return runs


def slice_bars(
    frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, period: pd.Timedelta
) -> pd.DataFrame:
    """Velas que **cierran** dentro de `(start, end]` (las que se pueden operar en el tramo)."""
    closes = pd.DatetimeIndex(frame.index) + period
    return frame.loc[(closes > start) & (closes <= end)]
