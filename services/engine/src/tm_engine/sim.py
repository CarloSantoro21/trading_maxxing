"""Ejecución simulada (`ENGINE_EXECUTION=sim`): llena las decisiones al precio de mercado.

Sirve para correr el sistema completo **sin llaves de Binance Spot Testnet** (demo local,
CI manual o mientras no hay cuenta de testnet). Recorre el mismo camino que el modo real:

    tm:decisions (Redis) ──▶ SimExecutionEngine ──▶ FillRecord ──▶ WorkerApiTradeRecorder
                                                                   (/internal/trades → D1)

Diferencias con el `TradingNode` de Nautilus (modo `testnet`):

- **Precio**: cada orden se llena completa, al instante, al último precio de la REST
  **pública** de Binance (`GET /api/v3/ticker/price`, sin llave). Si `api.binance.com` no
  responde o la red lo bloquea (p. ej. 451 desde EE. UU.), prueba `data-api.binance.vision`;
  si tampoco, usa el último precio conocido del símbolo y, al final, el `entry_price` de la
  decisión (el cierre de la vela que la originó).
- **Comisión**: la del laboratorio y de la SimulationGate, 0.1 % por lado
  (`ENGINE_SIM_FEE`), en USDT, descontada del P&L del sleeve igual que en el motor real.
- **Protección**: el SL y el TP de cada compra se vigilan con el mismo precio cada
  `bracket_check_secs` y, si se tocan, se vende toda la posición del sleeve (rol `sl`/`tp`).
- **Sin saldo del exchange**: no hay cuenta, así que no se revisa el efectivo libre; el
  tamaño ya lo acotan el BotRunner y el RiskAgent con el capital del perfil.

Las reglas de aceptación son las de `AgenticStrategy`: shadow nunca se ejecuta, la guardia de
sleeves solo deja **entrar** a estrategias `active`/`probation` (los cierres siempre pasan),
una decisión bloqueada por el RiskAgent se rechaza, en spot no se abren cortos y, sin
`target_quantity`, una compra con el sleeve abierto se rechaza y una venta cierra todo.
Cada fill sale con `execution="sim"`, que el recorder guarda en `rationale.execution` para
distinguir en la base y en el dashboard lo simulado de lo que pasó por Testnet.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any

import httpx
from tm_shared.contracts import DecisionEnvelope, OrderSide

from tm_engine.decisions import DecisionSource
from tm_engine.recorder import FillRecord, FillRecorder, MemoryFillRecorder, RestoredPosition
from tm_engine.sleeves import SleeveLedger, book_key, split_book_key

log = logging.getLogger(__name__)

#: Comisión por lado del laboratorio (`tm_engine.lab.config.LabConfig.fee`) y de la gate.
SIM_FEE = Decimal("0.001")
#: Paso de cantidad de BTCUSDT/ETHUSDT en Binance Spot (el RiskAgent ya cuantiza a esto).
QTY_STEP = Decimal("0.00001")
#: REST pública de Binance, en orden de preferencia (no requieren llave).
PUBLIC_PRICE_URLS: tuple[str, ...] = (
    "https://api.binance.com",
    "https://data-api.binance.vision",
)
TICKER_PATH = "/api/v3/ticker/price"
ZERO = Decimal("0")


class PublicPriceFeed:
    """Último precio de un símbolo desde la REST pública de Binance, con respaldo.

    Guarda el último precio bueno de cada símbolo: si ninguna URL responde, devuelve ese.
    Las respuestas se reutilizan durante `ttl` segundos para no gastar peso de la API.
    """

    def __init__(
        self,
        base_urls: Sequence[str] = PUBLIC_PRICE_URLS,
        *,
        client: httpx.Client | None = None,
        timeout: float = 5.0,
        ttl: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.base_urls = tuple(u.rstrip("/") for u in base_urls)
        self._client = client or httpx.Client(
            timeout=timeout, headers={"User-Agent": "trading_maxxing/tm-engine-sim"}
        )
        self._ttl = ttl
        self._clock = clock
        self._last: dict[str, tuple[Decimal, float]] = {}
        #: URL que respondió la última vez (diagnóstico para el heartbeat).
        self.source: str | None = None

    def remember(self, symbol: str, price: Decimal) -> None:
        """Registra un precio conocido (p. ej. el cierre de la vela de una decisión)."""
        if price > 0:
            self._last[symbol] = (Decimal(price), float("-inf"))

    def last_known(self, symbol: str) -> Decimal | None:
        hit = self._last.get(symbol)
        return hit[0] if hit else None

    def price(self, symbol: str) -> Decimal | None:
        """Precio actual de `symbol`; el último conocido si la red falla; `None` si nunca hubo."""
        cached = self._last.get(symbol)
        now = self._clock()
        if cached is not None and now - cached[1] < self._ttl:
            return cached[0]
        for base in self.base_urls:
            try:
                response = self._client.get(base + TICKER_PATH, params={"symbol": symbol})
                if response.status_code != 200:
                    log.warning("precio de %s en %s: HTTP %s", symbol, base, response.status_code)
                    continue
                price = Decimal(str(response.json()["price"]))
            except (httpx.HTTPError, KeyError, TypeError, ValueError, InvalidOperation) as exc:
                log.warning("precio de %s en %s falló: %s", symbol, base, exc)
                continue
            if price > 0:
                self._last[symbol] = (price, now)
                self.source = base
                return price
        if cached is not None:
            log.warning("sin precio en vivo de %s: se usa el último conocido %s", symbol, cached[0])
            self.source = "last_known"
            return cached[0]
        return None

    def close(self) -> None:
        self._client.close()


@dataclass(frozen=True)
class SimRejection:
    envelope: DecisionEnvelope
    reason: str


@dataclass
class _Protection:
    """SL y TP vigentes de una posición (los de la última compra) y su decisión."""

    stop_loss: Decimal | None
    take_profit: Decimal | None
    envelope: DecisionEnvelope


PriceSource = Callable[[str], Decimal | None]


class SimExecutionEngine:
    """Ejecuta `DecisionEnvelope` con fills simulados (ver el módulo)."""

    def __init__(
        self,
        source: DecisionSource | None = None,
        recorder: FillRecorder | None = None,
        *,
        prices: PublicPriceFeed | None = None,
        fee_rate: Decimal = SIM_FEE,
        sleeve_guard: Callable[[str], bool] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._source = source
        self.recorder: FillRecorder = recorder or MemoryFillRecorder()
        self.prices = prices or PublicPriceFeed()
        self.fee_rate = Decimal(fee_rate)
        self._sleeve_guard = sleeve_guard
        self._clock = clock
        self.ledger = SleeveLedger()
        self.rejections: list[SimRejection] = []
        self.accepted: list[DecisionEnvelope] = []
        self.fills = 0
        self._protection: dict[tuple[str, str], _Protection] = {}

    # --- decisiones ------------------------------------------------------------------------

    def poll(self) -> int:
        """Procesa lo pendiente en la fuente; devuelve cuántas decisiones llegaron."""
        if self._source is None:
            return 0
        envelopes = self._source.poll(self._clock())
        for envelope in envelopes:
            try:
                self.on_decision(envelope)
            except Exception as exc:  # una decisión rota no detiene a las demás
                log.exception("falló la decisión %s", envelope.id)
                self._reject(envelope, f"error al ejecutar: {exc}")
        return len(envelopes)

    def on_decision(self, envelope: DecisionEnvelope) -> bool:
        """Mismas reglas que `AgenticStrategy.on_decision`; True si hubo fill."""
        decision = envelope.decision
        if envelope.shadow:
            return self._reject(envelope, "decisión de una estrategia en shadow: no se ejecuta")
        closing = decision.side is OrderSide.SELL
        guard = self._sleeve_guard
        if not closing and guard is not None and not guard(envelope.sleeve):
            reason = getattr(guard, "reason", None)
            why = reason(envelope.sleeve) if callable(reason) else "el sleeve no opera capital"
            return self._reject(envelope, f"sin órdenes reales: {why}")
        risk = decision.rationale.risk
        if risk is not None and not risk.approved:
            return self._reject(envelope, "bloqueada por RiskAgent: " + "; ".join(risk.reasons))
        if decision.entry_price is not None:
            self.prices.remember(decision.symbol, Decimal(decision.entry_price))
        price = self.prices.price(decision.symbol)
        if price is None:
            return self._reject(envelope, f"sin precio de mercado para {decision.symbol}")
        if decision.side is OrderSide.BUY:
            return self._buy(envelope, price)
        return self._sell(envelope, price)

    def _buy(self, envelope: DecisionEnvelope, price: Decimal) -> bool:
        decision = envelope.decision
        key = (book_key(envelope.bot_id, envelope.sleeve), decision.symbol)
        if self.ledger.quantity(*key) > 0 and envelope.target_quantity is None:
            return self._reject(envelope, "el sleeve ya tiene una posición abierta en el símbolo")
        quantity = _floor(decision.quantity)
        if quantity <= 0:
            return self._reject(envelope, "la cantidad redondea a 0")
        self.accepted.append(envelope)
        self._protection[key] = _Protection(decision.stop_loss, decision.take_profit, envelope)
        self._fill(envelope, OrderSide.BUY, quantity, price, "entry")
        return True

    def _sell(self, envelope: DecisionEnvelope, price: Decimal) -> bool:
        decision = envelope.decision
        key = (book_key(envelope.bot_id, envelope.sleeve), decision.symbol)
        held = self.ledger.quantity(*key)
        if held <= 0:
            return self._reject(envelope, "sin posición en el sleeve; en spot no se abren cortos")
        target = envelope.target_quantity
        quantity = _floor(min(decision.quantity, held))
        if target is None or target <= 0 or quantity >= held:
            quantity, role = held, "exit"
        elif quantity <= 0:
            return self._reject(envelope, "la venta parcial redondea a 0 con la precisión del par")
        else:
            role = "reduce"
        self.accepted.append(envelope)
        self._fill(envelope, OrderSide.SELL, quantity, price, role)
        return True

    def _reject(self, envelope: DecisionEnvelope, reason: str) -> bool:
        log.warning(
            "decisión rechazada (%s): %s", book_key(envelope.bot_id, envelope.sleeve), reason
        )
        self.rejections.append(SimRejection(envelope, reason))
        return False

    # --- protección ------------------------------------------------------------------------

    def check_protection(self) -> int:
        """Vende a mercado las posiciones cuyo SL o TP ya tocó el precio; devuelve cuántas."""
        closed = 0
        for (book, symbol), prot in list(self._protection.items()):
            held = self.ledger.quantity(book, symbol)
            if held <= 0:
                self._protection.pop((book, symbol), None)
                continue
            price = self.prices.price(symbol)
            if price is None:
                continue
            if prot.stop_loss is not None and price <= prot.stop_loss:
                role = "sl"
            elif prot.take_profit is not None and price >= prot.take_profit:
                role = "tp"
            else:
                continue
            log.info("%s de %s en %s tocado a %s", role.upper(), book, symbol, price)
            self._fill(prot.envelope, OrderSide.SELL, held, price, role)
            closed += 1
        return closed

    # --- fills -----------------------------------------------------------------------------

    def _fill(
        self,
        envelope: DecisionEnvelope,
        side: OrderSide,
        quantity: Decimal,
        price: Decimal,
        role: str,
    ) -> FillRecord:
        bot_id, sleeve = envelope.bot_id, envelope.sleeve
        book = book_key(bot_id, sleeve)
        symbol = envelope.decision.symbol
        commission = (quantity * price * self.fee_rate).quantize(Decimal("0.00000001"))
        realized = self.ledger.apply_fill(
            sleeve=book,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            commission=commission,
        )
        remaining = self.ledger.quantity(book, symbol)
        if remaining <= 0:
            self._protection.pop((book, symbol), None)
        self.fills += 1
        record = FillRecord(
            sleeve=sleeve,
            symbol=symbol,
            side=side,
            role=role,
            quantity=quantity,
            price=price,
            commission=commission,
            realized_pnl=realized,
            position_after=remaining,
            client_order_id=f"SIM-{uuid.uuid4().hex[:16]}",
            ts=self._clock(),
            decision=envelope.decision,
            bot_id=bot_id,
            execution="sim",
        )
        log.info(
            "fill simulado %s %s %s %s %s @ %s (pnl %s, comisión %s)",
            role,
            book,
            side.value,
            quantity,
            symbol,
            price,
            realized,
            commission,
        )
        try:
            self.recorder.on_fill(record)
        except Exception as exc:
            log.error("no se pudo registrar el fill simulado: %s", exc)
        return record

    # --- reinicio y consultas --------------------------------------------------------------

    def restore_positions(self, positions: Iterable[RestoredPosition]) -> int:
        """Siembra el libro con las posiciones abiertas de `trades` (igual que el modo real)."""
        count = 0
        for pos in positions:
            if pos.quantity <= 0:
                continue
            book = book_key(pos.bot_id, pos.sleeve)
            self.ledger.restore(book, pos.symbol, pos.quantity, pos.avg_price)
            count += 1
        return count

    def sleeve_report(self) -> dict[str, dict[str, Any]]:
        return self.ledger.to_dict()

    def positions(self) -> list[dict[str, str | None]]:
        out: list[dict[str, str | None]] = []
        for book, stats in self.ledger.sleeves.items():
            bot_id, sleeve = split_book_key(book)
            for symbol, pos in stats.positions.items():
                if pos.quantity > 0:
                    out.append(
                        {
                            "bot_id": bot_id,
                            "sleeve": sleeve,
                            "symbol": symbol,
                            "quantity": str(pos.quantity),
                            "avg_price": str(pos.avg_price),
                        }
                    )
        return out


class SimRunner:
    """Hilo que llama `poll()` cada `interval` s y `check_protection()` cada `protect_every` s."""

    def __init__(
        self, engine: SimExecutionEngine, *, interval: float = 1.0, protect_every: float = 5.0
    ) -> None:
        self.engine = engine
        self.interval = interval
        self.protect_every = protect_every
        self.error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="tm-engine-sim", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        last_protect = float("-inf")
        while not self._stop.is_set():
            try:
                self.engine.poll()
                if time.monotonic() - last_protect >= self.protect_every:
                    last_protect = time.monotonic()
                    self.engine.check_protection()
                self.error = None
            except Exception as exc:
                self.error = f"el ciclo simulado falló: {exc}"
                log.exception("falló el ciclo de la ejecución simulada")
            self._stop.wait(self.interval)

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self._thread = None


def _floor(quantity: Decimal) -> Decimal:
    return Decimal(quantity).quantize(QTY_STEP, rounding=ROUND_DOWN)


__all__ = [
    "PUBLIC_PRICE_URLS",
    "SIM_FEE",
    "PublicPriceFeed",
    "SimExecutionEngine",
    "SimRejection",
    "SimRunner",
]
