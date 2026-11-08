"""AgenticStrategy: convierte las decisiones del orquestador en órdenes de Nautilus.

- Compra (`side=buy`): orden bracket. La entrada es market y, cuando se llena (OTO), quedan
  vivos el stop-loss (stop market) y el take-profit (limit) ligados como OUO/OCO: el que se
  llena cancela al otro. Ambos hijos son `reduce_only`. Cada bracket es un **tramo** del
  sleeve con su propia protección.
- Venta (`side=sell`): en spot significa reducir o salir a USDT. Nunca se abren cortos: una
  venta sin posición se rechaza.

Dos modos, según el sobre (`DecisionEnvelope.target_quantity`, ver `tm_engine.decisions`):

- **Sin `target_quantity`** (comportamiento del PR #69): una compra con el sleeve ya abierto se
  rechaza y una venta cierra la posición completa del sleeve (cancela su SL y TP y vende a
  mercado, `reduce_only`, toda la cantidad, aunque la decisión pida otra).
- **Con `target_quantity`** (posición objetivo del orquestador, #19): una compra con el sleeve
  abierto agrega un tramo nuevo (otro bracket) y una venta con objetivo mayor que 0 es
  **parcial**: vende `decision.quantity` (como máximo lo que tiene el sleeve) y achica la
  protección de los tramos, del más reciente al más viejo (`modify_order` de su SL y TP, o se
  cancelan si el tramo queda en 0), para que cada unidad siga protegida una sola vez. Con
  objetivo 0, o si la venta cubre toda la posición, se cierra completo como arriba.

Cada orden lleva los tags `sleeve:<nombre>`, `role:<entry|sl|tp|exit|reduce>` y, si la decisión
trae bot, `bot:<id>`, para atribuir los fills y el P&L a su bot y sleeve (ver `SleeveLedger`).
Las decisiones `shadow` se rechazan siempre.

Binance Spot no acepta listas de órdenes ligadas (OCO) desde el adaptador de Nautilus, así que
en vivo los hijos se emulan localmente (`emulation_trigger`) y la estrategia gestiona las
contingencias (`manage_contingent_orders=True`). En el venue simulado del backtest el motor de
matching soporta OTO/OUO de forma nativa.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide as NtOrderSide
from nautilus_trader.model.enums import TimeInForce, TriggerType
from nautilus_trader.model.events import OrderDenied, OrderFilled, OrderRejected
from nautilus_trader.model.identifiers import ClientOrderId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy
from tm_shared.contracts import OrderSide, TradeDecision

from tm_engine.decisions import DecisionEnvelope, DecisionSource
from tm_engine.instruments import instrument_id
from tm_engine.recorder import FillRecord, FillRecorder, MemoryFillRecorder
from tm_engine.sleeves import (
    SleeveLedger,
    book_key,
    bot_from_tags,
    bot_tag,
    sleeve_from_tags,
    sleeve_tag,
)

log = logging.getLogger(__name__)

ROLE_PREFIX = "role:"
POLL_TIMER = "tm-poll-decisions"
ZERO = Decimal("0")


class AgenticStrategyConfig(StrategyConfig, frozen=True):
    """Configuración de AgenticStrategy.

    bar_types: barras a las que se suscribe (en cada barra revisa si hay decisiones nuevas).
    emulation_trigger: nombre de `TriggerType` para emular SL/TP localmente (`NO_TRIGGER` en
        backtest, `BID_ASK` o `LAST_PRICE` en vivo con Binance Spot).
    poll_interval_secs: si es > 0, además revisa la fuente con un timer (útil en vivo).
    """

    bar_types: tuple[str, ...] = ()
    emulation_trigger: str = "NO_TRIGGER"
    poll_interval_secs: float = 0.0


@dataclass(frozen=True)
class Rejection:
    envelope: DecisionEnvelope
    reason: str


@dataclass
class _Tranche:
    """Un bracket del sleeve: su entrada, su protección y la cantidad que protege."""

    entry_id: str
    sl_id: str
    tp_id: str
    quantity: Decimal = ZERO  # cantidad llenada en la entrada que sigue protegida


@dataclass
class _OpenTrade:
    """Datos de la decisión vigente de un sleeve en un símbolo."""

    envelope: DecisionEnvelope
    order_ids: list[str] = field(default_factory=list)
    tranches: list[_Tranche] = field(default_factory=list)


def _role(tags: list[str] | None) -> str:
    for tag in tags or []:
        if tag.startswith(ROLE_PREFIX):
            return tag[len(ROLE_PREFIX) :]
    return "unknown"


def _tags(envelope: DecisionEnvelope, role: str) -> list[str]:
    tags = [sleeve_tag(envelope.sleeve), f"{ROLE_PREFIX}{role}"]
    if envelope.bot_id:
        tags.append(bot_tag(envelope.bot_id))
    return tags


def _book(envelope: DecisionEnvelope) -> str:
    return book_key(envelope.bot_id, envelope.sleeve)


class AgenticStrategy(Strategy):
    """Estrategia de Nautilus que ejecuta `TradeDecision` por bot y sleeve."""

    def __init__(
        self,
        config: AgenticStrategyConfig,
        source: DecisionSource | None = None,
        recorder: FillRecorder | None = None,
    ) -> None:
        super().__init__(config)
        self._source = source
        self.recorder: FillRecorder = recorder or MemoryFillRecorder()
        self.ledger = SleeveLedger()
        self.rejections: list[Rejection] = []
        self.accepted: list[DecisionEnvelope] = []
        self._open: dict[tuple[str, str], _OpenTrade] = {}
        self._emulation = TriggerType[config.emulation_trigger]
        self._last_close: dict[str, Decimal] = {}

    # --- ciclo de vida -----------------------------------------------------------------

    def on_start(self) -> None:
        for spec in self.config.bar_types:
            self.subscribe_bars(BarType.from_str(spec))
        if self.config.poll_interval_secs > 0:
            self.clock.set_timer(
                POLL_TIMER,
                interval=timedelta(seconds=self.config.poll_interval_secs),
                callback=lambda _event: self.poll_decisions(),
            )

    def on_bar(self, bar: Bar) -> None:
        self._last_close[bar.bar_type.instrument_id.symbol.value] = bar.close.as_decimal()
        self.poll_decisions()

    def on_stop(self) -> None:
        if self.config.poll_interval_secs > 0 and POLL_TIMER in self.clock.timer_names:
            self.clock.cancel_timer(POLL_TIMER)

    # --- decisiones ----------------------------------------------------------------------

    def poll_decisions(self) -> int:
        """Pide a la fuente las decisiones pendientes y las procesa; devuelve cuántas llegaron."""
        if self._source is None:
            return 0
        now = datetime.fromtimestamp(self.clock.timestamp_ns() / 1e9, tz=UTC)
        envelopes = self._source.poll(now)
        for envelope in envelopes:
            self.on_decision(envelope)
        return len(envelopes)

    def on_decision(self, envelope: DecisionEnvelope) -> bool:
        """Convierte una decisión en órdenes (el `on_signal` de la propuesta).

        Devuelve True si se enviaron órdenes; si no, la decisión queda en `rejections`.
        """
        decision = envelope.decision
        if envelope.shadow:
            return self._reject(envelope, "decisión de una estrategia en shadow: no se ejecuta")
        risk = decision.rationale.risk
        if risk is not None and not risk.approved:
            return self._reject(envelope, "bloqueada por RiskAgent: " + "; ".join(risk.reasons))
        instrument = self.cache.instrument(instrument_id(decision.symbol))
        if instrument is None:
            return self._reject(envelope, f"instrumento desconocido: {decision.symbol}")
        if decision.side is OrderSide.BUY:
            return self._open_long(envelope, instrument)
        return self._sell(envelope, instrument)

    def _reject(self, envelope: DecisionEnvelope, reason: str) -> bool:
        self.log.warning(f"decisión rechazada ({_book(envelope)}): {reason}")
        self.rejections.append(Rejection(envelope, reason))
        return False

    def _open_long(self, envelope: DecisionEnvelope, instrument: Instrument) -> bool:
        decision = envelope.decision
        key = (_book(envelope), decision.symbol)
        scale_in = envelope.target_quantity is not None
        is_open = key in self._open or self.ledger.quantity(*key) > 0
        if is_open and not scale_in:
            return self._reject(envelope, "el sleeve ya tiene una posición abierta en el símbolo")
        shortfall = self._cash_shortfall(decision, instrument)
        if shortfall is not None:
            return self._reject(envelope, shortfall)
        bracket = self.order_factory.bracket(
            instrument_id=instrument.id,
            order_side=NtOrderSide.BUY,
            quantity=instrument.make_qty(decision.quantity),
            emulation_trigger=self._emulation,
            sl_trigger_price=instrument.make_price(decision.stop_loss),
            tp_price=instrument.make_price(decision.take_profit),
            tp_post_only=False,
            time_in_force=TimeInForce.GTC,
            entry_tags=_tags(envelope, "entry"),
            sl_tags=_tags(envelope, "sl"),
            tp_tags=_tags(envelope, "tp"),
        )
        entry, sl, tp = (o.client_order_id.value for o in bracket.orders)
        trade = self._open.get(key)
        if trade is None:
            trade = self._open[key] = _OpenTrade(envelope, [entry, sl, tp])
        trade.tranches.append(_Tranche(entry_id=entry, sl_id=sl, tp_id=tp))
        self.accepted.append(envelope)
        self.submit_order_list(bracket)
        return True

    def _cash_shortfall(self, decision: TradeDecision, instrument: Instrument) -> str | None:
        """Revisa que alcance el saldo libre en la moneda de cotización (spot: sin margen).

        Usa el último cierre visto (o el `entry_price` de la decisión) más la comisión de
        taker. Sin precio de referencia no se puede revisar y se deja pasar.
        """
        price = self._last_close.get(decision.symbol, decision.entry_price)
        account = self.portfolio.account(instrument.id.venue)
        if price is None or account is None:
            return None
        needed = decision.quantity * price * (1 + instrument.taker_fee)
        balance = account.balance_free(instrument.quote_currency)
        free = balance.as_decimal() if balance is not None else Decimal("0")
        if needed > free:
            return f"saldo insuficiente: se necesitan {needed:.2f} y hay {free:.2f} libres"
        return None

    def _sell(self, envelope: DecisionEnvelope, instrument: Instrument) -> bool:
        decision = envelope.decision
        key = (_book(envelope), decision.symbol)
        held = self.ledger.quantity(*key)
        if held <= 0:
            return self._reject(envelope, "sin posición en el sleeve; en spot no se abren cortos")
        target = envelope.target_quantity
        # Hacia abajo a la precisión del par: nunca se vende más de lo pedido.
        step = Decimal(1).scaleb(-instrument.size_precision)
        quantity = min(decision.quantity, held).quantize(step, rounding=ROUND_DOWN)
        if target is None or target <= 0 or quantity >= held:
            return self._close_long(envelope, instrument, held)
        if quantity <= 0:
            return self._reject(envelope, "la venta parcial redondea a 0 con la precisión del par")
        return self._reduce_long(envelope, instrument, quantity)

    def _close_long(
        self, envelope: DecisionEnvelope, instrument: Instrument, held: Decimal
    ) -> bool:
        """Cierre completo: cancela la protección del sleeve y vende todo a mercado."""
        key = (_book(envelope), envelope.decision.symbol)
        self._cancel_sleeve_orders(envelope, instrument)
        order = self.order_factory.market(
            instrument_id=instrument.id,
            order_side=NtOrderSide.SELL,
            quantity=instrument.make_qty(held),
            reduce_only=True,
            tags=_tags(envelope, "exit"),
        )
        trade = self._open.get(key)
        if trade is not None:
            trade.order_ids.append(order.client_order_id.value)
            trade.tranches.clear()
        self.accepted.append(envelope)
        self.submit_order(order)
        return True

    def _reduce_long(
        self, envelope: DecisionEnvelope, instrument: Instrument, quantity: Decimal
    ) -> bool:
        """Venta parcial: achica la protección de los tramos (LIFO) y vende `quantity`."""
        key = (_book(envelope), envelope.decision.symbol)
        trade = self._open.get(key)
        left = quantity
        for tranche in reversed(trade.tranches if trade is not None else []):
            if left <= 0:
                break
            cut = min(left, tranche.quantity)
            if cut <= 0:
                continue
            tranche.quantity -= cut
            left -= cut
            self._resize_protection(tranche, instrument)
        order = self.order_factory.market(
            instrument_id=instrument.id,
            order_side=NtOrderSide.SELL,
            quantity=instrument.make_qty(quantity),
            reduce_only=True,
            tags=_tags(envelope, "reduce"),
        )
        if trade is not None:
            trade.order_ids.append(order.client_order_id.value)
            trade.tranches = [t for t in trade.tranches if t.quantity > 0]
        self.accepted.append(envelope)
        self.submit_order(order)
        return True

    def _resize_protection(self, tranche: _Tranche, instrument: Instrument) -> None:
        """Deja el SL y el TP del tramo con su nueva cantidad (o los cancela si es 0)."""
        for order_id in (tranche.sl_id, tranche.tp_id):
            order = self.cache.order(ClientOrderId(order_id))
            if order is None or order.is_closed:
                continue
            if tranche.quantity <= 0:
                self.cancel_order(order)
            else:
                self.modify_order(order, quantity=instrument.make_qty(tranche.quantity))

    def _cancel_sleeve_orders(self, envelope: DecisionEnvelope, instrument: Instrument) -> None:
        tag = sleeve_tag(envelope.sleeve)
        bot = envelope.bot_id

        def mine(tags: list[str] | None) -> bool:
            return tag in (tags or []) and bot_from_tags(tags) == bot

        for order in self.cache.orders_open(instrument_id=instrument.id, strategy_id=self.id):
            if mine(order.tags):
                self.cancel_order(order)
        for order in self.cache.orders_emulated(instrument_id=instrument.id, strategy_id=self.id):
            if mine(order.tags):
                self.cancel_order(order)

    # --- fills ---------------------------------------------------------------------------

    def on_order_filled(self, event: OrderFilled) -> None:
        order = self.cache.order(event.client_order_id)
        tags = order.tags if order is not None else None
        sleeve = sleeve_from_tags(tags)
        bot_id = bot_from_tags(tags)
        book = book_key(bot_id, sleeve)
        symbol = event.instrument_id.symbol.value
        side = OrderSide.BUY if event.order_side == NtOrderSide.BUY else OrderSide.SELL
        price = event.last_px.as_decimal()
        quantity = event.last_qty.as_decimal()
        commission = _commission_in_quote(event, price)
        realized = self.ledger.apply_fill(
            sleeve=book,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            commission=commission,
        )
        remaining = self.ledger.quantity(book, symbol)
        key = (book, symbol)
        trade = self._open.get(key)
        if trade is not None:
            self._track_tranche(trade, event.client_order_id.value, quantity)
        record = FillRecord(
            sleeve=sleeve,
            symbol=symbol,
            side=side,
            role=_role(tags),
            quantity=quantity,
            price=price,
            commission=commission,
            realized_pnl=realized,
            position_after=remaining,
            client_order_id=event.client_order_id.value,
            ts=datetime.fromtimestamp(event.ts_event / 1e9, tz=UTC),
            decision=trade.envelope.decision if trade else None,
            bot_id=bot_id,
        )
        if side is OrderSide.SELL and remaining == 0:
            self._open.pop(key, None)
        self.log.info(
            f"fill {record.role} {book} {side.value} {quantity} {symbol} @ {price} "
            f"(pnl {realized}, comisión {commission})"
        )
        try:
            self.recorder.on_fill(record)
        except Exception as exc:
            self.log.error(f"no se pudo registrar el fill: {exc}")

    @staticmethod
    def _track_tranche(trade: _OpenTrade, client_order_id: str, quantity: Decimal) -> None:
        """Lleva la cantidad protegida de cada tramo con los fills de su entrada y de su SL/TP."""
        for tranche in trade.tranches:
            if client_order_id == tranche.entry_id:
                tranche.quantity += quantity
                return
            if client_order_id in (tranche.sl_id, tranche.tp_id):
                tranche.quantity = max(ZERO, tranche.quantity - quantity)
                trade.tranches = [t for t in trade.tranches if t.quantity > 0]
                return

    def on_order_rejected(self, event: OrderRejected) -> None:
        self._release_failed_entry(event.client_order_id.value, f"rechazada: {event.reason}")

    def on_order_denied(self, event: OrderDenied) -> None:
        self._release_failed_entry(event.client_order_id.value, f"denegada: {event.reason}")

    def _release_failed_entry(self, client_order_id: str, reason: str) -> None:
        """Si la entrada de un tramo no llegó al venue, la quita; sin posición libera el sleeve."""
        for key, trade in list(self._open.items()):
            failed = [t for t in trade.tranches if t.entry_id == client_order_id]
            if not failed:
                continue
            trade.tranches = [t for t in trade.tranches if t.entry_id != client_order_id]
            if not trade.tranches and self.ledger.quantity(*key) <= 0:
                self._open.pop(key)
            self.rejections.append(Rejection(trade.envelope, f"orden de entrada {reason}"))
            self.log.warning(f"entrada de {key[0]} en {key[1]} {reason}")

    # --- consultas -----------------------------------------------------------------------

    def sleeve_report(self) -> dict[str, dict[str, Any]]:
        return self.ledger.to_dict()

    def protected_quantity(self, sleeve: str, symbol: str, bot_id: str | None = None) -> Decimal:
        """Cantidad cubierta por los SL/TP vivos del sleeve (para pruebas y diagnóstico)."""
        trade = self._open.get((book_key(bot_id, sleeve), symbol))
        return sum((t.quantity for t in trade.tranches), ZERO) if trade else ZERO


def _commission_in_quote(event: OrderFilled, price: Decimal) -> Decimal:
    """Comisión del fill en moneda de cotización (si viene en la base, se valúa al precio)."""
    commission = event.commission
    if commission is None:
        return Decimal("0")
    amount = commission.as_decimal()
    return amount if commission.currency == event.currency else amount * price
