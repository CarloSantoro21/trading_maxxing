"""Ejecución del tramo out-of-sample en Nautilus (`run_backtest`) y su equity a mercado.

Las órdenes del simulador se convierten en `TradeDecision` (compra = bracket con SL y TP de
catástrofe muy lejos, para que no se disparen; venta = salir a USDT) y se sueltan con
`ScheduledDecisionSource` al cierre de su vela. Se corre un motor por símbolo: si dos
símbolos cierran vela en el mismo instante, la decisión del segundo se procesaría antes de
que el motor viera su vela y se llenaría al cierre anterior.

El equity a mercado se reconstruye con los fills de Nautilus: efectivo (con comisiones) +
cantidad * cierre de cada vela.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

import pandas as pd
from tm_shared.contracts import OrderSide, Rationale, Signal, TradeDecision

from tm_engine.backtest import BacktestResult, run_backtest
from tm_engine.decisions import DecisionEnvelope, ScheduledDecisionSource
from tm_engine.lab.simulator import SimOrder, SimTrade, SleeveRun
from tm_engine.recorder import FillRecord

# SL y TP de catástrofe: la salida la decide la estrategia, no el bracket.
STOP_FRACTION = Decimal("0.05")
TAKE_MULTIPLE = Decimal("8")
MAX_PRICE = Decimal("999999")


@dataclass
class NautilusRun:
    """Resultado de Nautilus por sleeve, con el mismo formato que el simulador."""

    runs: list[SleeveRun]
    rejections: list[str]
    fills: int


def to_envelope(order: SimOrder, signal: Signal | None = None) -> DecisionEnvelope:
    """Orden del simulador -> `DecisionEnvelope` para `AgenticStrategy` (lógica de spot)."""
    price = Decimal(str(order.price))
    low = max(Decimal("0.01"), (price * STOP_FRACTION).quantize(Decimal("0.01")))
    high = min(MAX_PRICE, (price * TAKE_MULTIPLE).quantize(Decimal("0.01")))
    if order.side is OrderSide.BUY:
        stop_loss, take_profit = low, high
    else:
        stop_loss, take_profit = high, low  # en una venta el contrato pide TP < SL
    decision = TradeDecision(
        symbol=order.symbol,
        side=order.side,
        quantity=Decimal(str(order.quantity)),
        stop_loss=stop_loss,
        take_profit=take_profit,
        rationale=Rationale(signals=[signal] if signal else []),
        decided_at=order.ts.to_pydatetime(),
    )
    return DecisionEnvelope(sleeve=order.sleeve, decision=decision)


def equity_from_fills(
    bars: pd.DataFrame,
    fills: Sequence[FillRecord],
    *,
    sleeve: str,
    symbol: str,
    capital: float,
    period: pd.Timedelta,
) -> SleeveRun:
    """Equity por vela de un sleeve a partir de sus fills (posición valuada al cierre)."""
    closes_at = pd.DatetimeIndex(bars.index) + period
    close = bars["close"].to_numpy(dtype=float)
    mine = sorted(
        (f for f in fills if f.sleeve == sleeve and f.symbol == symbol), key=lambda f: f.ts
    )
    cash, qty = float(capital), 0.0
    cash_at, qty_at = [], []
    j = 0
    orders: list[SimOrder] = []
    for ts in closes_at:
        while j < len(mine) and pd.Timestamp(mine[j].ts) <= ts:
            f = mine[j]
            q, px, fee = float(f.quantity), float(f.price), float(f.commission)
            if f.side is OrderSide.BUY:
                cash -= q * px + fee
                qty += q
            else:
                cash += q * px - fee
                qty -= q
            orders.append(
                SimOrder(sleeve, symbol, f.side, pd.Timestamp(f.ts) - period, ts, q, px, fee)
            )
            j += 1
        cash_at.append(cash)
        qty_at.append(qty)
    equity = pd.Series(
        [c + q * p for c, q, p in zip(cash_at, qty_at, close, strict=True)],
        index=closes_at,
        name=sleeve,
    )
    return SleeveRun(sleeve, symbol, float(capital), equity, orders, _round_trips(orders, close))


def _round_trips(orders: list[SimOrder], close: Sequence[float]) -> list[SimTrade]:
    """Agrupa los fills (pueden ser parciales) en operaciones de ida y vuelta."""
    trades: list[SimTrade] = []
    qty = bought = notional = cost = fees = exit_value = 0.0
    entry_ts: pd.Timestamp | None = None
    for o in orders:
        if o.side is OrderSide.BUY:
            if qty <= 1e-12:
                entry_ts = o.ts
                bought = notional = cost = fees = exit_value = 0.0
            qty += o.quantity
            bought += o.quantity
            notional += o.quantity * o.price
            cost += o.quantity * o.price + o.commission
            fees += o.commission
            continue
        exit_value += o.quantity * o.price - o.commission
        fees += o.commission
        qty -= o.quantity
        if qty <= 1e-12 and entry_ts is not None:
            pnl = exit_value - cost
            exit_px = (exit_value + fees - (cost - notional)) / bought
            trades.append(
                SimTrade(
                    o.sleeve, o.symbol, entry_ts, o.ts, bought, notional / bought, exit_px, fees,
                    pnl, pnl / cost,
                )
            )  # fmt: skip
            entry_ts = None
    if entry_ts is not None and len(close):
        last = float(close[-1])
        pnl = qty * last - cost
        trades.append(
            SimTrade(
                orders[-1].sleeve, orders[-1].symbol, entry_ts, None, bought, notional / bought,
                last, fees, pnl, pnl / cost,
            )
        )  # fmt: skip
    return trades


def run_nautilus(
    frames: dict[str, pd.DataFrame],
    orders: Sequence[SimOrder],
    *,
    capital_by_sleeve: dict[tuple[str, str], float],
    timeframe: str,
    period: pd.Timedelta,
    fee: float,
) -> NautilusRun:
    """Corre las órdenes en `run_backtest` (un motor por símbolo) y reconstruye el equity."""
    runs: list[SleeveRun] = []
    rejections: list[str] = []
    n_fills = 0
    for symbol, bars in frames.items():
        envelopes = [to_envelope(o) for o in orders if o.symbol == symbol]
        start = sum(c for (_, sym), c in capital_by_sleeve.items() if sym == symbol)
        result: BacktestResult = run_backtest(
            {symbol: bars},
            ScheduledDecisionSource(envelopes),
            timeframe=timeframe,
            starting_usdt=Decimal(str(round(start, 2))),
            fee=Decimal(str(fee)),
            log_level="ERROR",
        )
        rejections.extend(result.rejections)
        n_fills += len(result.fills)
        for (sleeve, sym), capital in capital_by_sleeve.items():
            if sym != symbol:
                continue
            runs.append(
                equity_from_fills(
                    bars, result.fills, sleeve=sleeve, symbol=symbol, capital=capital,
                    period=period,
                )
            )  # fmt: skip
    return NautilusRun(runs=runs, rejections=rejections, fills=n_fills)
