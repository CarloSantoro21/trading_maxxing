"""AgenticStrategy: convierte las decisiones del orquestador en órdenes de Nautilus.

- Compra (`side=buy`): orden bracket. La entrada es market y, cuando se llena (OTO), quedan
  vivos el stop-loss (stop market) y el take-profit (limit) ligados como OUO/OCO: el que se
  llena cancela al otro. Ambos hijos son `reduce_only`.
- Venta (`side=sell`): en spot significa "quedarse en USDT" (flat). Se cancelan el SL y el TP
  pendientes del sleeve y se vende a mercado, `reduce_only`, toda la cantidad que tiene ese
  sleeve (ni más ni menos, aunque la decisión pida otra). Nunca se abren cortos: una venta sin
  posición se rechaza.

Cada orden lleva los tags `sleeve:<nombre>` y `role:<entry|sl|tp|exit>` para atribuir los
fills y el P&L a su sleeve (ver `SleeveLedger`).

Con un `sleeve_guard` (en vivo, `tm_agents.reference.TradingSleeveGuard`) solo se envían
órdenes de los sleeves cuya estrategia opera con capital (`active` o `probation`). Las
estrategias en `shadow` nunca llegan al exchange: su decisión se rechaza aquí y su
desempeño solo se lleva en el ledger de referencia.

Binance Spot no acepta listas de órdenes ligadas (OCO) desde el adaptador de Nautilus, así que
en vivo los hijos se emulan localmente (`emulation_trigger`) y la estrategia gestiona las
contingencias (`manage_contingent_orders=True`). En el venue simulado del backtest el motor de
matching soporta OTO/OUO de forma nativa.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide as NtOrderSide
from nautilus_trader.model.enums import TimeInForce, TriggerType
from nautilus_trader.model.events import OrderDenied, OrderFilled, OrderRejected
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy
from tm_shared.contracts import OrderSide, TradeDecision

from tm_engine.decisions import DecisionEnvelope, DecisionSource
from tm_engine.instruments import instrument_id
from tm_engine.recorder import FillRecord, FillRecorder, MemoryFillRecorder
from tm_engine.sleeves import SleeveLedger, sleeve_from_tags, sleeve_tag

log = logging.getLogger(__name__)

ROLE_PREFIX = "role:"
POLL_TIMER = "tm-poll-decisions"


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
class _OpenTrade:
    """Datos de la decisión vigente de un sleeve en un símbolo."""

    envelope: DecisionEnvelope
    order_ids: list[str] = field(default_factory=list)


def _role(tags: list[str] | None) -> str:
    for tag in tags or []:
        if tag.startswith(ROLE_PREFIX):
            return tag[len(ROLE_PREFIX) :]
    return "unknown"


class AgenticStrategy(Strategy):
    """Estrategia de Nautilus que ejecuta `TradeDecision` por sleeve."""

    def __init__(
        self,
        config: AgenticStrategyConfig,
        source: DecisionSource | None = None,
        recorder: FillRecorder | None = None,
        sleeve_guard: Callable[[str], bool] | None = None,
    ) -> None:
        super().__init__(config)
        self._source = source
        self._sleeve_guard = sleeve_guard
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
        if self._sleeve_guard is not None and not self._sleeve_guard(envelope.sleeve):
            reason = getattr(self._sleeve_guard, "reason", None)
            why = reason(envelope.sleeve) if callable(reason) else "el sleeve no opera capital"
            return self._reject(envelope, f"sin órdenes reales: {why}")
        risk = decision.rationale.risk
        if risk is not None and not risk.approved:
            return self._reject(envelope, "bloqueada por RiskAgent: " + "; ".join(risk.reasons))
        instrument = self.cache.instrument(instrument_id(decision.symbol))
        if instrument is None:
            return self._reject(envelope, f"instrumento desconocido: {decision.symbol}")
        if decision.side is OrderSide.BUY:
            return self._open_long(envelope, instrument)
        return self._close_long(envelope, instrument)

    def _reject(self, envelope: DecisionEnvelope, reason: str) -> bool:
        self.log.warning(f"decisión rechazada ({envelope.sleeve}): {reason}")
        self.rejections.append(Rejection(envelope, reason))
        return False

    def _open_long(self, envelope: DecisionEnvelope, instrument: Instrument) -> bool:
        decision = envelope.decision
        key = (envelope.sleeve, decision.symbol)
        if key in self._open or self.ledger.quantity(*key) > 0:
            return self._reject(envelope, "el sleeve ya tiene una posición abierta en el símbolo")
        shortfall = self._cash_shortfall(decision, instrument)
        if shortfall is not None:
            return self._reject(envelope, shortfall)
        tag = sleeve_tag(envelope.sleeve)
        bracket = self.order_factory.bracket(
            instrument_id=instrument.id,
            order_side=NtOrderSide.BUY,
            quantity=instrument.make_qty(decision.quantity),
            emulation_trigger=self._emulation,
            sl_trigger_price=instrument.make_price(decision.stop_loss),
            tp_price=instrument.make_price(decision.take_profit),
            tp_post_only=False,
            time_in_force=TimeInForce.GTC,
            entry_tags=[tag, f"{ROLE_PREFIX}entry"],
            sl_tags=[tag, f"{ROLE_PREFIX}sl"],
            tp_tags=[tag, f"{ROLE_PREFIX}tp"],
        )
        self._open[key] = _OpenTrade(envelope, [o.client_order_id.value for o in bracket.orders])
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

    def _close_long(self, envelope: DecisionEnvelope, instrument: Instrument) -> bool:
        decision = envelope.decision
        key = (envelope.sleeve, decision.symbol)
        held = self.ledger.quantity(*key)
        if held <= 0:
            return self._reject(envelope, "sin posición en el sleeve; en spot no se abren cortos")
        self._cancel_sleeve_orders(envelope.sleeve, instrument)
        order = self.order_factory.market(
            instrument_id=instrument.id,
            order_side=NtOrderSide.SELL,
            quantity=instrument.make_qty(held),
            reduce_only=True,
            tags=[sleeve_tag(envelope.sleeve), f"{ROLE_PREFIX}exit"],
        )
        trade = self._open.get(key)
        if trade is not None:
            trade.order_ids.append(order.client_order_id.value)
        self.accepted.append(envelope)
        self.submit_order(order)
        return True

    def _cancel_sleeve_orders(self, sleeve: str, instrument: Instrument) -> None:
        tag = sleeve_tag(sleeve)
        for order in self.cache.orders_open(instrument_id=instrument.id, strategy_id=self.id):
            if tag in (order.tags or []):
                self.cancel_order(order)
        for order in self.cache.orders_emulated(instrument_id=instrument.id, strategy_id=self.id):
            if tag in (order.tags or []):
                self.cancel_order(order)

    # --- fills ---------------------------------------------------------------------------

    def on_order_filled(self, event: OrderFilled) -> None:
        order = self.cache.order(event.client_order_id)
        tags = order.tags if order is not None else None
        sleeve = sleeve_from_tags(tags)
        symbol = event.instrument_id.symbol.value
        side = OrderSide.BUY if event.order_side == NtOrderSide.BUY else OrderSide.SELL
        price = event.last_px.as_decimal()
        quantity = event.last_qty.as_decimal()
        commission = _commission_in_quote(event, price)
        realized = self.ledger.apply_fill(
            sleeve=sleeve,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            commission=commission,
        )
        remaining = self.ledger.quantity(sleeve, symbol)
        key = (sleeve, symbol)
        trade = self._open.get(key)
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
        )
        if side is OrderSide.SELL and remaining == 0:
            self._open.pop(key, None)
        self.log.info(
            f"fill {record.role} {sleeve} {side.value} {quantity} {symbol} @ {price} "
            f"(pnl {realized}, comisión {commission})"
        )
        try:
            self.recorder.on_fill(record)
        except Exception as exc:
            self.log.error(f"no se pudo registrar el fill: {exc}")

    def on_order_rejected(self, event: OrderRejected) -> None:
        self._release_failed_entry(event.client_order_id.value, f"rechazada: {event.reason}")

    def on_order_denied(self, event: OrderDenied) -> None:
        self._release_failed_entry(event.client_order_id.value, f"denegada: {event.reason}")

    def _release_failed_entry(self, client_order_id: str, reason: str) -> None:
        """Si la entrada de un sleeve no llegó al venue, libera el sleeve para otra decisión."""
        for key, trade in list(self._open.items()):
            if trade.order_ids and trade.order_ids[0] == client_order_id:
                self._open.pop(key)
                self.rejections.append(Rejection(trade.envelope, f"orden de entrada {reason}"))
                self.log.warning(f"entrada de {key[0]} en {key[1]} {reason}")

    # --- consultas -----------------------------------------------------------------------

    def sleeve_report(self) -> dict[str, dict[str, Any]]:
        return self.ledger.to_dict()


def _commission_in_quote(event: OrderFilled, price: Decimal) -> Decimal:
    """Comisión del fill en moneda de cotización (si viene en la base, se valúa al precio)."""
    commission = event.commission
    if commission is None:
        return Decimal("0")
    amount = commission.as_decimal()
    return amount if commission.currency == event.currency else amount * price
