"""Ledger de referencia de una estrategia: su sleeve a tamaño completo, sin capital real.

Es el portafolio con el que la app juzga a cada estrategia (propuesta, sección 3.4): corre
con 100 % del sleeve y un capital nominal (100,000 USDT, igual que el laboratorio),
repartido por igual entre los símbolos, con las mismas señales que reciben los bots. Así el
LifecycleMonitor no depende del capital ni del nivel de riesgo de ningún usuario.

Reglas de ejecución (las mismas del simulador del laboratorio, ``tm_engine.lab.simulator``):

- Cada señal fija una exposición objetivo del sub-sleeve del símbolo. ``long`` = estar
  comprado y ``flat`` (o ``short``, que en spot no se opera) = quedarse en USDT. Con
  ``sizing="confidence"`` la exposición es la ``confidence`` de la señal (en el TrendAgent,
  la fracción de canales en largo); con ``sizing="full"`` un ``long`` invierte todo el
  sub-sleeve, como en el laboratorio de Kronos e Intraday Momentum.
- Solo se opera cuando cambia la exposición objetivo; el precio por sí solo no rebalancea.
- El fill es al **cierre de la vela** de la señal, con comisión ``fee`` (0.1 %) por lado
  sobre el nocional, cobrada en USDT.
- Un trade va de posición 0 a posición 0 (puede tener fills parciales) y su retorno es
  ``pnl / costo`` (el costo incluye la comisión de compra), como en el laboratorio.

El ledger es determinista y no tiene efectos: no lee reloj, red ni base de datos, y no
envía órdenes. Su estado se serializa con :meth:`ReferenceLedger.to_state`.
"""

from __future__ import annotations

import itertools
import math
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Literal

from tm_shared.contracts import Direction, OrderSide

from tm_agents.lifecycle import TradeRecord
from tm_agents.signals import SignalEvent

Sizing = Literal["confidence", "full"]

#: Capital nominal del ledger (USDT); el mismo del laboratorio.
REFERENCE_CAPITAL = 100_000.0
#: Comisión por lado del venue simulado de Binance Spot.
REFERENCE_FEE = 0.001
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT")
#: Exposición por estrategia igual a la de su laboratorio (las demás usan "full").
DEFAULT_SIZING: Mapping[str, Sizing] = {"trend": "confidence"}
#: Retornos de trades que se conservan en el estado (alcanzan para el Sharpe de 30 trades).
KEEP_RETURNS = 100
DAYS_PER_YEAR = 365
_EPS = 1e-9


@dataclass(frozen=True)
class ReferenceFill:
    """Un fill simulado al cierre de la vela."""

    strategy: str
    symbol: str
    side: OrderSide
    quantity: float
    price: float
    commission: float
    ts: datetime
    target: float


@dataclass(frozen=True)
class ReferenceTrade:
    """Un trade cerrado del ledger (de posición 0 a posición 0)."""

    strategy: str
    symbol: str
    opened_at: datetime
    closed_at: datetime
    quantity: float  # cantidad total comprada
    entry_price: float  # precio promedio de compra
    exit_price: float  # precio promedio de venta
    fees: float
    pnl: float  # neto de comisiones, en USDT
    ret: float  # pnl / costo de entrada (con comisiones)

    def to_record(self) -> TradeRecord:
        return TradeRecord(ret=self.ret, closed_at=self.closed_at)


@dataclass
class _Trip:
    """Trade abierto: acumula compras y ventas hasta que la posición vuelve a 0."""

    opened_at: datetime
    bought: float = 0.0
    notional: float = 0.0
    cost: float = 0.0
    sold: float = 0.0
    sold_notional: float = 0.0
    exit_value: float = 0.0
    fees: float = 0.0


@dataclass
class SymbolBook:
    """Sub-sleeve de un símbolo: efectivo, posición, último precio y exposición objetivo."""

    cash: float
    qty: float = 0.0
    price: float | None = None
    target: float = 0.0
    trip: _Trip | None = None

    @property
    def value(self) -> float:
        return self.qty * (self.price or 0.0)

    @property
    def equity(self) -> float:
        return self.cash + self.value


@dataclass(frozen=True)
class SignalResult:
    """Lo que dejó una señal en el ledger."""

    fills: list[ReferenceFill] = field(default_factory=list)
    closed: list[ReferenceTrade] = field(default_factory=list)


def target_exposure(event: SignalEvent, sizing: Sizing) -> float:
    """Exposición objetivo (0 a 1) del sub-sleeve según la señal."""
    sig = event.signal
    if sig.direction is not Direction.LONG:
        return 0.0
    return 1.0 if sizing == "full" else float(min(1.0, max(0.0, sig.confidence)))


def daily_sharpe(equity: Sequence[float]) -> float | None:
    """Sharpe anualizado (365 días, tasa 0) de una serie diaria; igual al del laboratorio."""
    if len(equity) < 3:
        return None
    rets = [b / a - 1.0 for a, b in itertools.pairwise(equity) if a > 0]
    if len(rets) < 2:
        return None
    mean = math.fsum(rets) / len(rets)
    var = math.fsum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    std = math.sqrt(var)
    if not math.isfinite(std) or std <= 1e-12:
        return None
    return mean / std * math.sqrt(DAYS_PER_YEAR)


def max_drawdown(equity: Iterable[float]) -> float:
    """Peor caída desde un máximo previo, como fracción positiva."""
    peak, worst = -math.inf, 0.0
    for e in equity:
        peak = max(peak, e)
        if peak > 0:
            worst = max(worst, 1.0 - e / peak)
    return worst


class ReferenceLedger:
    """Portafolio de referencia de una estrategia (ver el docstring del módulo).

    Args:
        strategy: nombre de la estrategia (el de ``strategies.name``).
        started_at: inicio de la corrida (entrada a shadow); separa las corridas de una
            misma estrategia (por ejemplo, tras una revalidación).
        capital: capital nominal total, repartido por igual entre ``symbols``.
        fee: comisión por lado.
        symbols: símbolos que opera.
        sizing: ``"confidence"`` o ``"full"``; por defecto el de :data:`DEFAULT_SIZING`.
    """

    def __init__(
        self,
        strategy: str,
        started_at: datetime,
        *,
        capital: float = REFERENCE_CAPITAL,
        fee: float = REFERENCE_FEE,
        symbols: Sequence[str] = DEFAULT_SYMBOLS,
        sizing: Sizing | None = None,
    ) -> None:
        if capital <= 0:
            raise ValueError("capital debe ser positivo")
        if not 0 <= fee < 0.1:
            raise ValueError("fee debe estar en [0, 0.1)")
        if not symbols:
            raise ValueError("el ledger necesita al menos un símbolo")
        self.strategy = strategy
        self.started_at = _utc(started_at)
        self.capital = float(capital)
        self.fee = float(fee)
        self.sizing: Sizing = sizing or DEFAULT_SIZING.get(strategy, "full")
        share = self.capital / len(symbols)
        self.books: dict[str, SymbolBook] = {s: SymbolBook(cash=share) for s in symbols}
        self.peak_equity = self.capital
        self.daily: dict[date, float] = {}
        self.recent_returns: deque[TradeRecord] = deque(maxlen=KEEP_RETURNS)
        self.n_trades = 0
        self.n_wins = 0
        self.commissions = 0.0
        self.realized_pnl = 0.0
        self.last_signal_at: datetime | None = None
        self.last_timeframe: str | None = None
        self.as_of: datetime = self.started_at

    # --- consultas -----------------------------------------------------------------------

    @property
    def equity(self) -> float:
        return math.fsum(b.equity for b in self.books.values())

    @property
    def cash(self) -> float:
        return math.fsum(b.cash for b in self.books.values())

    @property
    def positions_value(self) -> float:
        return math.fsum(b.value for b in self.books.values())

    @property
    def drawdown(self) -> float:
        return max(0.0, 1.0 - self.equity / self.peak_equity) if self.peak_equity > 0 else 0.0

    def daily_series(self) -> list[tuple[date, float]]:
        """Equity diaria (último valor de cada día UTC), del día más viejo al más reciente."""
        return sorted(self.daily.items())

    def equity_curve(self) -> list[float]:
        """Capital inicial seguido del equity de cada día: la base de las métricas."""
        return [self.capital, *(v for _, v in self.daily_series())]

    # --- señales -------------------------------------------------------------------------

    def on_signal(self, event: SignalEvent) -> SignalResult:
        """Aplica una señal de ``tm:signals``: valúa el símbolo al cierre de la vela
        (``event.price`` en ``event.bar_close``) y rebalancea si cambió el objetivo.

        Raises:
            ValueError: si la señal no es de esta estrategia o el símbolo no está en el
                ledger.
        """
        if event.strategy != self.strategy:
            raise ValueError(f"la señal es de '{event.strategy}', no de '{self.strategy}'")
        book = self.books.get(event.symbol)
        if book is None:
            raise ValueError(f"'{self.strategy}' no opera {event.symbol}")
        ts = event.bar_close
        price = float(event.price)
        book.price = price
        self.last_signal_at = ts
        self.last_timeframe = event.signal.timeframe.value
        target = target_exposure(event, self.sizing)
        result = SignalResult()
        if abs(target - book.target) > _EPS:
            self._rebalance(event.symbol, book, target, price, ts, result)
            book.target = target
        self.mark(ts)
        return result

    def mark(self, ts: datetime) -> float:
        """Valúa el ledger con los últimos precios y guarda el punto del día de ``ts``."""
        ts = _utc(ts)
        self.as_of = max(self.as_of, ts)
        equity = self.equity
        self.peak_equity = max(self.peak_equity, equity)
        self.daily[ts.date()] = equity
        return equity

    def _rebalance(
        self,
        symbol: str,
        book: SymbolBook,
        target: float,
        price: float,
        ts: datetime,
        result: SignalResult,
    ) -> None:
        equity = book.equity
        delta_value = target * equity - book.value
        if target <= _EPS:
            qty = book.qty
            side = OrderSide.SELL
        elif delta_value > 0:
            spend = min(delta_value, book.cash / (1.0 + self.fee))
            qty = spend / price
            side = OrderSide.BUY
        else:
            qty = min(book.qty, -delta_value / price)
            side = OrderSide.SELL
        if qty <= _EPS:
            return
        notional = qty * price
        commission = notional * self.fee
        self.commissions += commission
        result.fills.append(
            ReferenceFill(self.strategy, symbol, side, qty, price, commission, ts, target)
        )
        if side is OrderSide.BUY:
            book.cash -= notional + commission
            book.qty += qty
            trip = book.trip or _Trip(opened_at=ts)
            book.trip = trip
            trip.bought += qty
            trip.notional += notional
            trip.cost += notional + commission
            trip.fees += commission
            return
        book.cash += notional - commission
        book.qty -= qty
        trip = book.trip
        if trip is None:  # no debería pasar: no hay ventas sin compra previa
            return
        trip.sold += qty
        trip.sold_notional += notional
        trip.exit_value += notional - commission
        trip.fees += commission
        if book.qty <= _EPS:
            book.qty = 0.0
            book.trip = None
            result.closed.append(self._close_trip(symbol, trip, ts))

    def _close_trip(self, symbol: str, trip: _Trip, ts: datetime) -> ReferenceTrade:
        pnl = trip.exit_value - trip.cost
        trade = ReferenceTrade(
            strategy=self.strategy,
            symbol=symbol,
            opened_at=trip.opened_at,
            closed_at=ts,
            quantity=trip.bought,
            entry_price=trip.notional / trip.bought,
            exit_price=trip.sold_notional / trip.sold,
            fees=trip.fees,
            pnl=pnl,
            ret=pnl / trip.cost,
        )
        self.n_trades += 1
        self.n_wins += pnl > 0
        self.realized_pnl += pnl
        self.recent_returns.append(trade.to_record())
        return trade

    # --- métricas ------------------------------------------------------------------------

    def metrics(self) -> dict[str, Any]:
        """Métricas comparables con las del laboratorio (mismas definiciones)."""
        curve = self.equity_curve()
        days = len(curve) - 1
        total = self.equity / self.capital - 1.0
        # Con menos de 30 días el CAGR anualizado no dice nada.
        cagr = (1.0 + total) ** (DAYS_PER_YEAR / days) - 1.0 if days >= 30 and total > -1 else None
        return {
            "capital": self.capital,
            "equity": self.equity,
            "cash": self.cash,
            "positions_value": self.positions_value,
            "peak_equity": self.peak_equity,
            "drawdown": self.drawdown,
            "max_drawdown": max(max_drawdown(curve), self.drawdown),
            "total_return": total,
            "cagr": cagr,
            "sharpe": daily_sharpe(curve),
            "days": days,
            "trades": self.n_trades,
            "win_rate": self.n_wins / self.n_trades if self.n_trades else None,
            "realized_pnl": self.realized_pnl,
            "commissions": self.commissions,
            "exposure": {
                s: (b.value / b.equity if b.equity > 0 else 0.0) for s, b in self.books.items()
            },
            "sizing": self.sizing,
            "fee": self.fee,
        }

    # --- estado --------------------------------------------------------------------------

    def to_state(self) -> dict[str, Any]:
        """Estado serializable a JSON para retomar el ledger después de un reinicio."""
        return {
            "started_at": self.started_at.isoformat(),
            "capital": self.capital,
            "fee": self.fee,
            "sizing": self.sizing,
            "peak_equity": self.peak_equity,
            "books": {
                s: {
                    "cash": b.cash,
                    "qty": b.qty,
                    "price": b.price,
                    "target": b.target,
                    "trip": None
                    if b.trip is None
                    else {**vars(b.trip), "opened_at": b.trip.opened_at.isoformat()},
                }
                for s, b in self.books.items()
            },
            "daily": {d.isoformat(): v for d, v in self.daily_series()},
            "recent_returns": [
                [t.ret, t.closed_at.isoformat() if t.closed_at else None]
                for t in self.recent_returns
            ],
            "n_trades": self.n_trades,
            "n_wins": self.n_wins,
            "commissions": self.commissions,
            "realized_pnl": self.realized_pnl,
            "last_signal_at": self.last_signal_at.isoformat() if self.last_signal_at else None,
            "last_timeframe": self.last_timeframe,
            "as_of": self.as_of.isoformat(),
        }

    @classmethod
    def from_state(cls, strategy: str, state: Mapping[str, Any]) -> ReferenceLedger:
        """Reconstruye el ledger guardado con :meth:`to_state`."""
        books_state = state["books"]
        ledger = cls(
            strategy,
            _parse(state["started_at"]),
            capital=float(state["capital"]),
            fee=float(state["fee"]),
            symbols=list(books_state),
            sizing=state.get("sizing"),
        )
        for symbol, raw in books_state.items():
            book = ledger.books[symbol]
            book.cash = float(raw["cash"])
            book.qty = float(raw["qty"])
            book.price = None if raw.get("price") is None else float(raw["price"])
            book.target = float(raw.get("target", 0.0))
            trip = raw.get("trip")
            if trip:
                book.trip = _Trip(**{**trip, "opened_at": _parse(trip["opened_at"])})
        ledger.peak_equity = float(state["peak_equity"])
        ledger.daily = {date.fromisoformat(d): float(v) for d, v in state["daily"].items()}
        ledger.recent_returns.extend(
            TradeRecord(ret=float(r), closed_at=_parse(c) if c else None)
            for r, c in state.get("recent_returns", [])
        )
        ledger.n_trades = int(state.get("n_trades", 0))
        ledger.n_wins = int(state.get("n_wins", 0))
        ledger.commissions = float(state.get("commissions", 0.0))
        ledger.realized_pnl = float(state.get("realized_pnl", 0.0))
        last = state.get("last_signal_at")
        ledger.last_signal_at = _parse(last) if last else None
        ledger.last_timeframe = state.get("last_timeframe")
        ledger.as_of = _parse(state.get("as_of") or state["started_at"])
        return ledger


def _utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        raise ValueError("las fechas del ledger deben tener zona horaria (UTC)")
    return ts.astimezone(UTC)


def _parse(value: str | datetime) -> datetime:
    return _utc(value if isinstance(value, datetime) else datetime.fromisoformat(value))
