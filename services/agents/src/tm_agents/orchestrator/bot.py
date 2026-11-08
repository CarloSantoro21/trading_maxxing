"""Capa por usuario: un BotRunner por bot que convierte señales en órdenes de su tamaño.

Cada ``SignalEvent`` de ``tm:signals`` trae la señal, el veto y la simulación ya calculados
(una vez para todos). El BotRunner solo decide **cuánto**:

1. **Peso del sleeve**: ``PortfolioAllocator`` sobre el ``catalog`` del evento (inversa de la
   volatilidad validada, correlación, horizonte del perfil y estado: ``probation`` recibe la
   mitad). El capital del sleeve en un símbolo es ``equity * peso / número de instrumentos``
   (el laboratorio reparte cada sleeve por igual entre BTC y ETH).
2. **Exposición del nivel**: :func:`~tm_agents.orchestrator.exposure.exposure_for_level` con el
   drawdown p95 de la estrategia y su curva de sensibilidad (no la regla proporcional pura).
3. **Posición objetivo** = capital del sleeve * exposición * confianza (0 si la señal no es
   ``long``). En el TrendAgent la confianza es la fracción de canales Donchian en largo, así
   que cada canal mueve 1/4 del sleeve: hay compras y ventas **parciales**.

El BotRunner recuerda qué fracción de la posición objetivo tiene cada sleeve. Si la confianza
sube compra la diferencia (pasa por el ``RiskAgent`` con los límites de su nivel y el bloqueo
de noticias y del gate); si baja vende la parte proporcional de lo que tiene (reducir riesgo
nunca se bloquea); si llega a 0 cierra todo. Si no cambia, no opera (así no hay rebalanceos
diarios por el movimiento del precio, igual que en el laboratorio).

Cada decisión sale en un ``DecisionEnvelope`` con ``bot_id``, ``sleeve`` y ``target_quantity``
hacia ``tm:decisions``. Las estrategias en **shadow** hacen el mismo cálculo (con el peso que
tendrían si fueran activas) sobre un libro aparte y se publican en ``tm:shadow`` con
``shadow=true``: nunca llegan al motor.

El libro del bot (efectivo, posiciones y equity a mercado) es **virtual y optimista**: supone
que cada decisión se llena al precio del evento con la comisión de 0.1 %. Es lo que usa para
dimensionar y para los límites del RiskAgent; el P&L real lo lleva el engine por bot y sleeve.
Al arrancar (o reanudar tras un ``stop``) el supervisor lo reconstruye con :meth:`BotRunner.restore`
desde la tabla ``trades``, que es la fuente de verdad de lo que el motor ejecutó
(ver :mod:`tm_agents.orchestrator.state`).

Las salidas por SL/TP **reales** (Kronos) las ejecuta el motor con su bracket; el libro
virtual las refleja cuando el precio de un evento posterior del símbolo cruza el nivel
(:meth:`VirtualBook.settle_brackets`). Es una aproximación: el libro no lee los fills del
motor.

Comandos (``tm:commands``): ``start`` reanuda, ``stop`` deja de operar (las posiciones se
quedan) y ``kill`` cierra todas las posiciones a mercado y detiene el bot. El kill switch
por drawdown del RiskAgent hace lo mismo que ``kill``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum
from typing import Any

from tm_shared.contracts import (
    DecisionEnvelope,
    Direction,
    Horizon,
    OrderSide,
    Rationale,
    RiskVerdict,
    TradeDecision,
)

from tm_agents.allocator import AllocationResult, PortfolioAllocator
from tm_agents.orchestrator.exposure import exposure_for_level, sensitivity_for
from tm_agents.orchestrator.policy import policy_for, sell_levels
from tm_agents.orchestrator.state import BotState
from tm_agents.orchestrator.streams import DecisionSink
from tm_agents.risk import Portfolio, RiskAgent, RiskLimits, TradeProposal
from tm_agents.signals import SignalEvent, StrategySnapshot
from tm_agents.strategy_stats import StrategyState

log = logging.getLogger(__name__)

ZERO = Decimal("0")
SPOT_FEE = Decimal("0.001")
#: Cambio mínimo de la fracción objetivo que provoca una operación.
FRACTION_EPS = 1e-9


class BotStatus(StrEnum):
    """Mismos valores que el enum ``bot_status`` de la base."""

    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"
    KILLED = "killed"


class CommandType(StrEnum):
    START = "start"
    STOP = "stop"
    KILL = "kill"


@dataclass(frozen=True)
class BotCommand:
    """Una entrada de ``tm:commands`` (la publica la API, #21)."""

    type: CommandType
    bot_id: str
    reason: str = ""
    user_id: str | None = None
    id: str | None = None

    @classmethod
    def from_fields(cls, fields: Mapping[str, str], entry_id: str | None = None) -> BotCommand:
        return cls(
            type=CommandType(fields["type"]),
            bot_id=fields["bot_id"],
            reason=fields.get("reason", ""),
            user_id=fields.get("user_id"),
            id=entry_id,
        )


@dataclass(frozen=True)
class BotProfile:
    """Perfil de inversión del dueño del bot (tabla ``profiles``)."""

    risk_level: int
    capital: Decimal
    max_drawdown: float
    horizon: Horizon = Horizon.MEDIUM
    instruments: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")

    def __post_init__(self) -> None:
        if self.risk_level not in range(1, 6):
            raise ValueError("risk_level debe estar entre 1 y 5")
        if Decimal(self.capital) <= 0:
            raise ValueError("capital debe ser mayor a 0")
        if not self.instruments:
            raise ValueError("el perfil necesita al menos un instrumento")
        object.__setattr__(self, "capital", Decimal(self.capital))
        object.__setattr__(self, "horizon", Horizon(self.horizon))
        object.__setattr__(self, "instruments", tuple(self.instruments))


@dataclass(frozen=True)
class BotConfig:
    """Un bot de la tabla ``bots`` con su perfil."""

    bot_id: str
    profile: BotProfile
    strategy_config: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    status: BotStatus = BotStatus.RUNNING
    name: str = ""

    def enabled(self, strategy: str) -> bool:
        """Todas las estrategias vivas operan salvo que el bot apague una (``enabled: false``)."""
        cfg = self.strategy_config.get(strategy)
        return True if cfg is None else bool(cfg.get("enabled", True))


@dataclass
class SleevePosition:
    quantity: Decimal = ZERO
    fraction: float = 0.0  # fracción de la posición objetivo completa que se tiene (0 a 1)
    avg_price: Decimal = ZERO
    #: SL y TP reales del último tramo (solo estrategias con stop real, como Kronos).
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None


@dataclass
class Blocked:
    """Una entrada que el bot no hizo y por qué (veto, gate, RiskAgent, mínimo de Binance)."""

    event_id: str
    sleeve: str
    symbol: str
    reasons: list[str]
    shadow: bool = False


class VirtualBook:
    """Efectivo, posiciones por sleeve y equity a mercado del bot (ver el módulo)."""

    def __init__(self, capital: Decimal, fee: Decimal = SPOT_FEE) -> None:
        self.cash = Decimal(capital)
        self.fee = fee
        self.positions: dict[tuple[str, str], SleevePosition] = {}
        self.prices: dict[str, Decimal] = {}
        self.peak_equity = Decimal(capital)
        self.day_start_equity = Decimal(capital)
        self.day: str | None = None
        self.trades_today = 0

    def restore(self, state: BotState, capital: Decimal) -> None:
        """Deja el libro como lo dejó el motor antes de un reinicio (ver ``orchestrator.state``).

        Las posiciones sin ``target_fraction`` (filas anteriores al #89) se toman completas
        (fracción 1): la siguiente señal con menos confianza vende la parte proporcional.
        """
        self.positions.clear()
        cost = ZERO
        realized = state.closed_pnl
        for open_pos in state.positions:
            fraction = open_pos.target_fraction
            self.positions[(open_pos.sleeve, open_pos.symbol)] = SleevePosition(
                quantity=open_pos.quantity,
                fraction=1.0 if fraction is None else min(1.0, max(0.0, fraction)),
                avg_price=open_pos.avg_price,
                stop_loss=open_pos.stop_loss,
                take_profit=open_pos.take_profit,
            )
            cost += open_pos.quantity * open_pos.avg_price
            realized += open_pos.realized_pnl
        self.cash = Decimal(capital) + realized - cost
        self.peak_equity = max(Decimal(capital) + state.peak_closed_pnl, self.equity())
        self.day_start_equity = self.equity()
        self.day = None
        self.trades_today = 0

    def position(self, sleeve: str, symbol: str) -> SleevePosition:
        return self.positions.setdefault((sleeve, symbol), SleevePosition())

    def mark(self, symbol: str, price: Decimal, now: datetime) -> None:
        self.prices[symbol] = price
        day = now.date().isoformat()
        if day != self.day:
            self.day = day
            self.day_start_equity = self.equity()
            self.trades_today = 0
        self.peak_equity = max(self.peak_equity, self.equity())

    def exposure(self) -> Decimal:
        return sum(
            (p.quantity * self.prices.get(sym, p.avg_price) for (_, sym), p in self._open()),
            ZERO,
        )

    def equity(self) -> Decimal:
        return self.cash + self.exposure()

    def apply(self, sleeve: str, symbol: str, side: OrderSide, qty: Decimal, price: Decimal):
        pos = self.position(sleeve, symbol)
        notional = qty * price
        fee = notional * self.fee
        if side is OrderSide.BUY:
            total = pos.quantity + qty
            pos.avg_price = (pos.avg_price * pos.quantity + notional) / total
            pos.quantity = total
            self.cash -= notional + fee
        else:
            qty = min(qty, pos.quantity)
            pos.quantity -= qty
            self.cash += qty * price - fee
            if pos.quantity <= 0:
                pos.quantity, pos.fraction, pos.avg_price = ZERO, 0.0, ZERO
                pos.stop_loss = pos.take_profit = None
        self.trades_today += 1

    def settle_brackets(self, symbol: str, price: Decimal) -> list[tuple[str, Decimal]]:
        """Cierra en el libro los sleeves con stop real cuyo SL o TP ya tocó ``price``.

        El motor ejecuta esos SL/TP por su cuenta (bracket), así que aquí no se emite nada:
        solo se refleja la salida para que el sleeve pueda volver a entrar. Se liquida al
        nivel del stop o del objetivo. Devuelve ``(sleeve, precio)`` de cada cierre.
        """
        out: list[tuple[str, Decimal]] = []
        for (sleeve, sym), pos in self._open():
            if sym != symbol or pos.stop_loss is None or pos.take_profit is None:
                continue
            level = (
                pos.stop_loss
                if price <= pos.stop_loss
                else pos.take_profit
                if price >= pos.take_profit
                else None
            )
            if level is None:
                continue
            self.apply(sleeve, sym, OrderSide.SELL, pos.quantity, level)
            self.trades_today -= 1  # la salida la hizo el motor, no cuenta como decisión nueva
            out.append((sleeve, level))
        return out

    def portfolio(self) -> Portfolio:
        return Portfolio(
            equity=self.equity(),
            peak_equity=self.peak_equity,
            day_start_equity=self.day_start_equity,
            cash=self.cash,
            exposure=self.exposure(),
            trades_today=self.trades_today,
            open_symbols=frozenset(sym for (_, sym), _p in self._open()),
        )

    def held(self) -> list[tuple[str, str, SleevePosition]]:
        return [(sleeve, sym, p) for (sleeve, sym), p in self._open()]

    def _open(self):
        return [(k, p) for k, p in sorted(self.positions.items()) if p.quantity > 0]


class BotRunner:
    """Capa por usuario de un bot (ver el módulo).

    Args:
        config: bot y perfil.
        sink: destino de las decisiones reales (``tm:decisions``).
        shadow_sink: destino de las decisiones de estrategias en shadow (``tm:shadow``).
        allocator: PortfolioAllocator (pesos globales por estrategia).
        limits: límites del RiskAgent; por omisión, los del nivel del perfil.
        fee: comisión por lado que usa el libro virtual.
    """

    def __init__(
        self,
        config: BotConfig,
        sink: DecisionSink,
        *,
        shadow_sink: DecisionSink | None = None,
        allocator: PortfolioAllocator | None = None,
        limits: RiskLimits | None = None,
        fee: Decimal = SPOT_FEE,
    ) -> None:
        self.config = config
        self.bot_id = config.bot_id
        self.sink = sink
        self.shadow_sink = shadow_sink
        self.allocator = allocator or PortfolioAllocator()
        profile = config.profile
        self.limits = limits or RiskLimits.from_profile(profile.risk_level, profile.max_drawdown)
        self.risk = RiskAgent(self.limits)
        self.book = VirtualBook(profile.capital, fee)
        self.shadow_book = VirtualBook(profile.capital, fee)
        self.status = config.status
        self.decisions: list[DecisionEnvelope] = []
        self.shadow_decisions: list[DecisionEnvelope] = []
        self.blocked: list[Blocked] = []
        self.kill_reason: str | None = None

    # --- control ---------------------------------------------------------------------------
    def restore(self, state: BotState) -> int:
        """Reconstruye el libro real del bot desde ``trades``; devuelve las posiciones abiertas.

        Solo las estrategias con stop real (Kronos) conservan su SL/TP en el libro (para
        :meth:`VirtualBook.settle_brackets`); en las demás el bracket es de catástrofe y la
        salida la decide la señal.
        """
        if state.bot_id != self.bot_id:
            raise ValueError(f"estado de {state.bot_id} para el bot {self.bot_id}")
        self.book.restore(state, self.config.profile.capital)
        for (sleeve, _symbol), pos in self.book.positions.items():
            if not policy_for(sleeve).signal_levels:
                pos.stop_loss = pos.take_profit = None
        held = self.book.held()
        if held:
            log.info(
                "bot %s restaurado desde trades: %s",
                self.bot_id,
                ", ".join(f"{s}:{sym}={p.quantity}" for s, sym, p in held),
            )
        return len(held)

    @property
    def running(self) -> bool:
        return self.status is BotStatus.RUNNING

    def start(self) -> None:
        if self.status is BotStatus.KILLED:
            self.kill_reason = None
        self.status = BotStatus.RUNNING

    def stop(self) -> None:
        self.status = BotStatus.STOPPED

    def kill(self, now: datetime, reason: str = "kill") -> list[DecisionEnvelope]:
        """Cierra todas las posiciones a mercado y detiene el bot."""
        out: list[DecisionEnvelope] = []
        for sleeve, symbol, pos in self.book.held():
            price = self.book.prices.get(symbol, pos.avg_price)
            envelope = self._sell(sleeve, symbol, pos.quantity, ZERO, price, now, reason, None)
            out.append(self._emit(envelope, shadow=False))
            self.book.apply(sleeve, symbol, OrderSide.SELL, pos.quantity, price)
        self.shadow_book.positions.clear()
        self.status = BotStatus.KILLED
        self.kill_reason = reason
        log.warning("bot %s detenido (%s): %d cierres", self.bot_id, reason, len(out))
        return out

    def handle_command(self, command: BotCommand, now: datetime) -> list[DecisionEnvelope]:
        if command.bot_id != self.bot_id:
            return []
        if command.type is CommandType.START:
            self.start()
        elif command.type is CommandType.STOP:
            self.stop()
        elif command.type is CommandType.KILL:
            return self.kill(now, f"kill: {command.reason}".strip(": ") or "kill")
        return []

    # --- señales ---------------------------------------------------------------------------
    def on_event(self, event: SignalEvent) -> list[DecisionEnvelope]:
        """Procesa un evento de ``tm:signals``; devuelve lo que se publicó (reales y shadow)."""
        if not self.running or event.symbol not in self.config.profile.instruments:
            return []
        for book in (self.book, self.shadow_book):
            book.mark(event.symbol, event.price, event.bar_close)
            book.settle_brackets(event.symbol, event.price)
        if self.risk.check_drawdown(self.book.portfolio()):
            dd = self.risk.drawdown(self.book.portfolio())
            return self.kill(event.bar_close, f"kill switch: drawdown {dd:.2%}")

        out = self._close_orphans(event)
        if not self.config.enabled(event.strategy):
            return out
        shadow = event.is_shadow
        book = self.shadow_book if shadow else self.book
        snapshot = event.snapshot()
        if snapshot is None:
            return out
        target, weight, exposure = self._target_fraction(event, snapshot, book)
        pos = book.position(event.strategy, event.symbol)
        if target <= FRACTION_EPS and pos.quantity > 0:
            out.append(self._exit(event, book, pos, ZERO, shadow, "señal sin largo"))
        elif pos.quantity > 0 and target < pos.fraction - FRACTION_EPS:
            out.extend(self._reduce(event, book, pos, target, shadow))
        elif target > pos.fraction + FRACTION_EPS:
            sleeve_capital = book.equity() * Decimal(str(weight)) / len(self._instruments())
            out.extend(
                self._increase(event, book, pos, target, sleeve_capital, weight, exposure, shadow)
            )
        return out

    # --- tamaño ----------------------------------------------------------------------------
    def allocation(self, catalog: Sequence[StrategySnapshot], what_if: str | None = None):
        """Pesos del PortfolioAllocator; ``what_if`` trata esa estrategia shadow como activa."""
        stats = [
            s.to_stats(
                state=StrategyState.ACTIVE
                if s.name == what_if and s.state is StrategyState.SHADOW
                else None
            )
            for s in catalog
        ]
        result: AllocationResult = self.allocator.allocate_detailed(
            self.book.equity(), self.config.profile.horizon, stats
        )
        return result

    def _target_fraction(
        self, event: SignalEvent, snapshot: StrategySnapshot, book: VirtualBook
    ) -> tuple[float, float, float]:
        """``(fracción objetivo, peso del sleeve, exposición del nivel)``."""
        signal = event.signal
        confidence = signal.confidence if signal.direction is Direction.LONG else 0.0
        if confidence <= 0:
            return 0.0, 0.0, 0.0
        what_if = event.strategy if event.is_shadow else None
        weight = self.allocation(event.catalog, what_if).weights.get(event.strategy, 0.0)
        exposure = exposure_for_level(
            self.config.profile.risk_level,
            snapshot.max_drawdown_p95,
            sensitivity_for(snapshot.name, snapshot.sensitivity),
        )
        if weight <= 0 or exposure <= 0:
            return 0.0, weight, exposure
        return confidence, weight, exposure

    def _instruments(self) -> tuple[str, ...]:
        return self.config.profile.instruments

    # --- operaciones -----------------------------------------------------------------------
    def _increase(
        self,
        event: SignalEvent,
        book: VirtualBook,
        pos: SleevePosition,
        target: float,
        sleeve_capital: Decimal,
        weight: float,
        exposure: float,
        shadow: bool,
    ) -> list[DecisionEnvelope]:
        delta = target - pos.fraction
        notional = sleeve_capital * Decimal(str(exposure)) * Decimal(str(delta))
        wanted = (notional / event.price).quantize(self.limits.qty_step, rounding=ROUND_DOWN)
        rationale = self._rationale(event, weight, exposure, target)
        reasons: list[str] = []
        if event.veto is not None:
            reasons.append(f"veto por noticias ({event.veto.scope}): {event.veto.reason}")
        if event.sim is None:
            reasons.append(
                "SimulationGate sin resultado" + (f": {event.sim_error}" if event.sim_error else "")
            )
        if reasons:
            return self._block(event, reasons, shadow)
        proposal = TradeProposal(
            symbol=event.symbol,
            side=OrderSide.BUY,
            entry_price=event.price,
            stop_loss=event.stop_loss,
            take_profit=event.take_profit,
            rationale=rationale,
        )
        decision, verdict = self.risk.evaluate_target(
            proposal, book.portfolio(), wanted, cap_by_stop=event.hard_stop
        )
        if decision is None:
            return self._block(event, verdict.reasons, shadow)
        decision = decision.model_copy(update={"decided_at": event.bar_close})
        # Si el RiskAgent recortó la cantidad, la fracción avanza en proporción.
        filled = float(decision.quantity / wanted) if wanted > 0 else 0.0
        envelope = DecisionEnvelope(
            id=f"{self.bot_id}:{event.id}",
            bot_id=self.bot_id,
            sleeve=event.strategy,
            target_quantity=pos.quantity + decision.quantity,
            shadow=shadow,
            decision=decision,
        )
        book.apply(event.strategy, event.symbol, OrderSide.BUY, decision.quantity, event.price)
        pos.fraction = min(1.0, pos.fraction + delta * min(1.0, filled))
        if event.hard_stop:
            pos.stop_loss, pos.take_profit = event.stop_loss, event.take_profit
        return [self._emit(envelope, shadow=shadow)]

    def _reduce(
        self,
        event: SignalEvent,
        book: VirtualBook,
        pos: SleevePosition,
        target: float,
        shadow: bool,
    ) -> list[DecisionEnvelope]:
        keep = Decimal(str(target / pos.fraction))
        qty = (pos.quantity * (1 - keep)).quantize(self.limits.qty_step, rounding=ROUND_DOWN)
        remaining = pos.quantity - qty
        if remaining * event.price < self.limits.min_notional:
            return [self._exit(event, book, pos, ZERO, shadow, "el resto no alcanza el mínimo")]
        if qty * event.price < self.limits.min_notional:
            return self._block(
                event,
                [f"venta parcial de {qty * event.price:.2f} USDT < mínimo de Binance"],
                shadow,
            )
        envelope = self._sell(
            event.strategy,
            event.symbol,
            qty,
            remaining,
            event.price,
            event.bar_close,
            f"la confianza bajó de {pos.fraction:.2f} a {target:.2f}",
            event,
        )
        book.apply(event.strategy, event.symbol, OrderSide.SELL, qty, event.price)
        pos.fraction = target
        return [self._emit(envelope, shadow=shadow)]

    def _exit(
        self,
        event: SignalEvent,
        book: VirtualBook,
        pos: SleevePosition,
        target: Decimal,
        shadow: bool,
        why: str,
    ) -> DecisionEnvelope:
        qty = pos.quantity
        envelope = self._sell(
            event.strategy, event.symbol, qty, target, event.price, event.bar_close, why, event
        )
        book.apply(event.strategy, event.symbol, OrderSide.SELL, qty, event.price)
        return self._emit(envelope, shadow=shadow)

    def _close_orphans(self, event: SignalEvent) -> list[DecisionEnvelope]:
        """Cierra los sleeves de estrategias que ya no están vivas (retiradas) en el símbolo."""
        alive = {s.name for s in event.catalog}
        out: list[DecisionEnvelope] = []
        for sleeve, symbol, pos in self.book.held():
            if symbol != event.symbol or sleeve in alive:
                continue
            envelope = self._sell(
                sleeve,
                symbol,
                pos.quantity,
                ZERO,
                event.price,
                event.bar_close,
                "la estrategia ya no está activa",
                None,
            )
            self.book.apply(sleeve, symbol, OrderSide.SELL, pos.quantity, event.price)
            out.append(self._emit(envelope, shadow=False))
        return out

    def _sell(
        self,
        sleeve: str,
        symbol: str,
        qty: Decimal,
        target: Decimal,
        price: Decimal,
        now: datetime,
        why: str,
        event: SignalEvent | None,
    ) -> DecisionEnvelope:
        stop_loss, take_profit = sell_levels(price)
        rationale = Rationale(
            signals=[event.signal] if event is not None else [],
            news_veto=False,
            risk=RiskVerdict(approved=True, reasons=["reduce riesgo"]),
            text=f"venta de {qty} {symbol} en {sleeve}: {why}",
        )
        decision = TradeDecision(
            symbol=symbol,
            side=OrderSide.SELL,
            quantity=qty,
            entry_price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            rationale=rationale,
            decided_at=now,
        )
        suffix = event.id if event is not None else f"{sleeve}:{symbol}:{now.isoformat()}"
        return DecisionEnvelope(
            id=f"{self.bot_id}:{suffix}:sell",
            bot_id=self.bot_id,
            sleeve=sleeve,
            target_quantity=target,
            decision=decision,
        )

    def _rationale(
        self, event: SignalEvent, weight: float, exposure: float, target: float
    ) -> Rationale:
        veto = event.veto
        return Rationale(
            signals=[event.signal],
            weights={event.strategy: weight, "exposure": exposure, "target_fraction": target},
            news_veto=veto is not None,
            news_summary=veto.reason if veto is not None else None,
            sim=event.sim,
            text=(
                f"{event.strategy} {event.signal.direction.value} con confianza "
                f"{event.signal.confidence:.2f}: sleeve al {weight:.1%} del equity, "
                f"exposición del nivel {self.config.profile.risk_level} de {exposure:.1%}"
            ),
        )

    def _block(self, event: SignalEvent, reasons: list[str], shadow: bool) -> list:
        self.blocked.append(Blocked(event.id, event.strategy, event.symbol, reasons, shadow))
        log.info("bot %s no entra en %s: %s", self.bot_id, event.id, "; ".join(reasons))
        return []

    def _emit(self, envelope: DecisionEnvelope, *, shadow: bool) -> DecisionEnvelope:
        if shadow:
            envelope = envelope.model_copy(update={"shadow": True})
            self.shadow_decisions.append(envelope)
            if self.shadow_sink is not None:
                self.shadow_sink.publish(envelope)
            return envelope
        self.decisions.append(envelope)
        self.sink.publish(envelope)
        return envelope

    # --- consultas -------------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        return {
            "bot_id": self.bot_id,
            "status": self.status.value,
            "equity": str(self.book.equity()),
            "cash": str(self.book.cash),
            "positions": {
                f"{sleeve}:{symbol}": {"quantity": str(p.quantity), "fraction": p.fraction}
                for sleeve, symbol, p in self.book.held()
            },
            "kill_reason": self.kill_reason,
        }


__all__ = [
    "Blocked",
    "BotCommand",
    "BotConfig",
    "BotProfile",
    "BotRunner",
    "BotStatus",
    "CommandType",
    "SleevePosition",
    "VirtualBook",
]
