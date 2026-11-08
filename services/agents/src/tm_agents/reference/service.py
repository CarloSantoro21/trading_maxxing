"""ReferenceService: un ledger de referencia por estrategia y el LifecycleMonitor cableado.

Es parte de la capa compartida (propuesta, sección 3.4): corre una vez para toda la app, no
por usuario. En cada ciclo (:meth:`ReferenceService.run_once`):

1. Sincroniza con el ``StrategyRegistry`` las estrategias en ``shadow``, ``active`` y
   ``probation``. Cada una tiene su :class:`ReferenceLedger`, que nace cuando la estrategia
   entra a shadow y sigue igual al pasar a activa o a observación.
2. Lee las señales nuevas de ``tm:signals`` (el mismo :class:`~tm_agents.signals.SignalEvent`
   que consumen los BotRunner, con su propio cursor) y las aplica a su ledger (fill a
   ``price``, el cierre de la vela, con 0.1 % de comisión).
3. Cada vez que se cierra un trade de referencia, y una vez al día, llama a
   ``LifecycleMonitor.check`` con las estadísticas del ledger (no con el P&L de ningún
   usuario). Si propone una transición la aplica con
   ``StrategyRegistry.transition(actor='system')`` y la deja en el log.
4. Guarda los trades en ``reference_trades``, la equity diaria en ``reference_equity`` y las
   métricas en vivo en ``strategies.live_metrics``.

Este servicio **nunca envía órdenes**: no tiene cliente del exchange ni escribe en
``tm:decisions``. Las estrategias en shadow solo existen aquí; el motor, además, rechaza
cualquier decisión de un sleeve sin capital (:class:`TradingSleeveGuard`).

Días en shadow sin fallas (para la página #58): cuentan desde la entrada a shadow o desde
la última falla, lo que sea más reciente. Una falla es una señal que no se pudo aplicar
(de otra estrategia, símbolo desconocido, error inesperado) o un silencio de más de
``silence_bars`` velas de su timeframe sin señales.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from tm_agents.lifecycle import LifecycleMonitor, LiveStats, Transition, recent_sharpe
from tm_agents.montecarlo.report import DistributionSummary
from tm_agents.reference.ledger import (
    DEFAULT_SYMBOLS,
    REFERENCE_CAPITAL,
    REFERENCE_FEE,
    ReferenceLedger,
    ReferenceTrade,
    Sizing,
)
from tm_agents.reference.store import EquityPoint, ReferenceStore, ReferenceStoreError, trade_row
from tm_agents.registry import (
    SYSTEM_ACTOR,
    RegistryError,
    StrategyRecord,
    StrategyRegistry,
    TransitionError,
)
from tm_agents.signals import SignalEvent, SignalSource
from tm_agents.strategy_stats import StrategyState

log = logging.getLogger(__name__)

S = StrategyState
#: Llave de Redis con el último id de ``tm:signals`` que procesó (y guardó) este servicio.
REFERENCE_CHECKPOINT = "tm:reference:last_signal_id"
#: Estados que tienen ledger de referencia.
TRACKED_STATES: tuple[StrategyState, ...] = (S.SHADOW, S.ACTIVE, S.PROBATION)
#: Días mínimos en shadow sin fallas antes de que una persona pueda promoverla.
MIN_SHADOW_DAYS = 7
#: Duración de cada timeframe (para detectar silencios).
TIMEFRAME_PERIOD = {
    "1m": timedelta(minutes=1),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
}
#: Sin ninguna señal todavía, cuánto se espera antes de marcar falla.
FIRST_SIGNAL_GRACE = timedelta(days=2)


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------------------
# Banda del Monte Carlo del laboratorio
# ---------------------------------------------------------------------------------------
@dataclass(frozen=True)
class DrawdownBand:
    """Lo que ``LifecycleMonitor.check`` usa del ``MCReport``: la distribución del drawdown.

    El reporte completo (con sus arreglos) no se guarda en la base; de ``lab_report`` basta
    con los percentiles del drawdown máximo.
    """

    max_drawdown: DistributionSummary
    sharpe_oos: float | None = None

    @classmethod
    def from_lab_report(cls, report: Mapping[str, Any] | None) -> DrawdownBand | None:
        """Lee el p95 y el p99 del drawdown del reporte del laboratorio.

        Acepta el formato del walk-forward (``montecarlo.max_drawdown.p95``) y el plano
        (``max_drawdown_p95`` / ``max_drawdown_p99``). Devuelve ``None`` si no los trae.
        """
        if not report:
            return None
        mc = report.get("montecarlo")
        dist = mc.get("max_drawdown") if isinstance(mc, Mapping) else None
        if isinstance(dist, Mapping) and "p95" in dist and "p99" in dist:
            p95, p99 = float(dist["p95"]), float(dist["p99"])
            p5 = float(dist.get("p5", p95))
            p50 = float(dist.get("p50", p95))
            mean = float(dist.get("mean", p50))
            top = float(dist.get("max", p99))
        elif "max_drawdown_p95" in report and "max_drawdown_p99" in report:
            p95, p99 = float(report["max_drawdown_p95"]), float(report["max_drawdown_p99"])
            p5 = p50 = mean = p95
            top = p99
        else:
            return None
        summary = DistributionSummary(
            mean=mean, std=0.0, min=p5, max=top, p5=p5, p50=p50, p95=p95, p99=p99
        )
        sharpe = report.get("sharpe_oos")
        if sharpe is None and isinstance(report.get("aggregate"), Mapping):
            sharpe = report["aggregate"].get("sharpe")
        return cls(summary, float(sharpe) if sharpe is not None else None)


# ---------------------------------------------------------------------------------------
# Estado por estrategia
# ---------------------------------------------------------------------------------------
@dataclass
class ShadowHealth:
    """Fallas de la estrategia en la corrida actual del ledger."""

    failures: int = 0
    last_failure_at: datetime | None = None
    last_failure_reason: str | None = None

    def fail(self, at: datetime, reason: str) -> None:
        self.failures += 1
        self.last_failure_at = at
        self.last_failure_reason = reason[:500]

    def to_dict(self) -> dict[str, Any]:
        return {
            "failures": self.failures,
            "last_failure_at": self.last_failure_at.isoformat() if self.last_failure_at else None,
            "last_failure_reason": self.last_failure_reason,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> ShadowHealth:
        raw = raw or {}
        last = raw.get("last_failure_at")
        return cls(
            failures=int(raw.get("failures", 0)),
            last_failure_at=datetime.fromisoformat(last) if last else None,
            last_failure_reason=raw.get("last_failure_reason"),
        )


@dataclass
class Tracked:
    """Una estrategia con ledger de referencia."""

    record: StrategyRecord
    ledger: ReferenceLedger
    health: ShadowHealth = field(default_factory=ShadowHealth)
    pending_trades: list[dict[str, Any]] = field(default_factory=list)
    # Último punto de cada día por guardar (con el estado de la estrategia en ese momento).
    dirty_days: dict[date, EquityPoint] = field(default_factory=dict)
    dirty: bool = True
    silence_reported_for: datetime | None = None

    @property
    def name(self) -> str:
        return self.record.name


def shadow_status(
    tracked: Tracked, as_of: datetime, min_days: int = MIN_SHADOW_DAYS
) -> dict[str, Any]:
    """Días en shadow sin fallas y si ya se puede promover (lo que muestra la página #58)."""
    started = tracked.ledger.started_at
    last_failure = tracked.health.last_failure_at
    clean_since = max(started, last_failure) if last_failure else started
    in_shadow = tracked.record.state is S.SHADOW
    days_clean = max(0.0, (as_of - clean_since).total_seconds() / 86_400)
    return {
        "started_at": started.isoformat(),
        "clean_since": clean_since.isoformat(),
        "days": max(0.0, (as_of - started).total_seconds() / 86_400),
        "days_clean": days_clean,
        "min_days": min_days,
        "eligible_for_promotion": in_shadow and days_clean >= min_days,
        **tracked.health.to_dict(),
    }


# ---------------------------------------------------------------------------------------
# Servicio
# ---------------------------------------------------------------------------------------
class ReferenceService:
    """Ledgers de referencia de todas las estrategias en shadow, active y probation.

    Args:
        registry: registro de estrategias (estado, ``lab_report`` y ``live_metrics``).
        store: dónde se guardan trades y equity diaria.
        source: fuente de señales de :mod:`tm_agents.signals` (``RedisSignalSource`` con
            ``checkpoint_key=REFERENCE_CHECKPOINT`` en vivo o ``ListSignalSource``).
        monitor: LifecycleMonitor; por defecto con los umbrales de la propuesta.
        capital: capital nominal de cada ledger (100,000 USDT).
        fee: comisión por lado (0.1 %).
        symbols: símbolos de cada ledger.
        sizing: exposición por estrategia (``confidence`` o ``full``).
        silence_bars: velas de su timeframe sin señales que cuentan como falla.
        clock: reloj (UTC); se inyecta en las pruebas.
    """

    def __init__(
        self,
        registry: StrategyRegistry,
        store: ReferenceStore,
        source: SignalSource,
        *,
        monitor: LifecycleMonitor | None = None,
        capital: float = REFERENCE_CAPITAL,
        fee: float = REFERENCE_FEE,
        symbols: Sequence[str] = DEFAULT_SYMBOLS,
        sizing: Mapping[str, Sizing] | None = None,
        silence_bars: int = 3,
        min_shadow_days: int = MIN_SHADOW_DAYS,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        if silence_bars < 1:
            raise ValueError("silence_bars debe ser al menos 1")
        self.registry = registry
        self.store = store
        self.source = source
        self.monitor = monitor or LifecycleMonitor()
        self.capital = capital
        self.fee = fee
        self.symbols = tuple(symbols)
        self.sizing = dict(sizing or {})
        self.silence_bars = silence_bars
        self.min_shadow_days = min_shadow_days
        self.clock = clock
        self.tracked: dict[str, Tracked] = {}
        self.transitions: list[tuple[str, Transition]] = []
        self.ignored = 0
        self._last_daily: date | None = None

    # --- ciclo ---------------------------------------------------------------------------

    def run_once(self, now: datetime | None = None) -> int:
        """Un ciclo completo; devuelve cuántas señales se aplicaron."""
        now = now or self.clock()
        self.sync(now)
        applied = 0
        for event in self.source.poll():
            applied += self.handle(event, now)
        if self._last_daily != now.date():
            self.daily(now)
            self._last_daily = now.date()
        self.flush(now)
        if not any(t.dirty for t in self.tracked.values()):
            commit = getattr(self.source, "commit", None)
            if callable(commit):
                commit()  # todo quedó guardado: se confirma lo leído del stream
        return applied

    def sync(self, now: datetime | None = None) -> None:
        """Alinea los ledgers con los estados del registro."""
        now = now or self.clock()
        try:
            records = self.registry.list(*TRACKED_STATES)
        except RegistryError as exc:
            log.warning("no se pudo leer el registro de estrategias: %s", exc)
            return
        current = {r.name: r for r in records}
        for name in list(self.tracked):
            if name not in current:
                log.info("'%s' ya no está en shadow/active/probation; se deja de seguir", name)
                del self.tracked[name]
        for name, record in current.items():
            tracked = self.tracked.get(name)
            if tracked is None:
                self.tracked[name] = self._load(record)
            elif record.state is S.SHADOW and record.state_changed_at > tracked.ledger.started_at:
                # Volvió a entrar a shadow (revalidación): corrida nueva.
                self.tracked[name] = self._new(record, record.state_changed_at)
            else:
                tracked.record = record

    def handle(self, event: SignalEvent, now: datetime | None = None) -> int:
        """Aplica una señal a su ledger; devuelve 1 si se aplicó y 0 si no."""
        now = now or self.clock()
        tracked = self.tracked.get(event.strategy)
        if tracked is None or tracked.record.state not in TRACKED_STATES:
            self.ignored += 1
            return 0
        if event.bar_close < tracked.ledger.started_at:
            self.ignored += 1  # señal de antes de la corrida actual
            return 0
        try:
            result = tracked.ledger.on_signal(event)
        except ValueError as exc:
            self._fail(tracked, now, f"señal no aplicada: {exc}")
            return 0
        except Exception as exc:  # una falla de una estrategia no tumba a las demás
            log.exception("error inesperado al aplicar la señal de '%s'", tracked.name)
            self._fail(tracked, now, f"error inesperado: {exc}")
            return 0
        tracked.silence_reported_for = None
        tracked.dirty = True
        self._snapshot(tracked, event.bar_close.date())
        for trade in result.closed:
            self._on_trade_closed(tracked, trade)
        return 1

    def daily(self, now: datetime | None = None) -> None:
        """Cierre del día: punto de equity, silencios y revisión del LifecycleMonitor."""
        now = now or self.clock()
        for tracked in list(self.tracked.values()):
            if tracked.record.state not in TRACKED_STATES:
                continue
            tracked.ledger.mark(now)
            self._snapshot(tracked, now.date())
            tracked.dirty = True
            self._check_silence(tracked, now)
            self.check(tracked, now)

    def flush(self, now: datetime | None = None) -> None:
        """Guarda trades, equity diaria y métricas de los ledgers que cambiaron."""
        now = now or self.clock()
        for tracked in self.tracked.values():
            if not tracked.dirty:
                continue
            ok = self._save_trades(tracked) and self._save_equity(tracked)
            try:
                self.registry.record_live_metrics(tracked.name, self.live_metrics(tracked, now))
            except RegistryError as exc:
                log.warning("no se pudieron guardar las métricas de '%s': %s", tracked.name, exc)
                ok = False
            tracked.dirty = not ok

    # --- LifecycleMonitor ----------------------------------------------------------------

    def check(self, tracked: Tracked, as_of: datetime) -> Transition | None:
        """Corre el monitor contra el ledger y aplica la transición que proponga."""
        band = DrawdownBand.from_lab_report(tracked.record.lab_report)
        if band is None:
            if tracked.record.state.gets_capital:
                log.warning(
                    "'%s' no tiene la distribución del drawdown en lab_report; el monitor "
                    "no la puede juzgar",
                    tracked.name,
                )
            return None
        ledger = tracked.ledger
        stats = LiveStats(
            equity=ledger.equity,
            peak_equity=ledger.peak_equity,
            recent_trades=list(ledger.recent_returns),
            state_entered_at=tracked.record.state_changed_at,
            as_of=as_of,
            current_drawdown=ledger.drawdown,
        )
        transition = self.monitor.check(tracked.record.state, stats, band)
        if transition is None:
            return None
        try:
            self.registry.transition(
                tracked.name, transition.to_state, SYSTEM_ACTOR, transition.reason
            )
        except (TransitionError, RegistryError) as exc:
            log.warning(
                "no se pudo aplicar %s -> %s a '%s': %s",
                transition.from_state.value,
                transition.to_state.value,
                tracked.name,
                exc,
            )
            self._refresh(tracked)
            return None
        log.warning(
            "LifecycleMonitor: '%s' %s -> %s (actor=system): %s",
            tracked.name,
            transition.from_state.value,
            transition.to_state.value,
            transition.reason,
        )
        self.transitions.append((tracked.name, transition))
        self._refresh(tracked)
        tracked.dirty = True
        return transition

    # --- métricas ------------------------------------------------------------------------

    def live_metrics(self, tracked: Tracked, now: datetime) -> dict[str, Any]:
        """Lo que se guarda en ``strategies.live_metrics``."""
        ledger = tracked.ledger
        metrics = ledger.metrics()
        trades = list(ledger.recent_returns)
        window = self.monitor.thresholds.sharpe_window
        sharpe_recent = recent_sharpe(trades, window)
        band = DrawdownBand.from_lab_report(tracked.record.lab_report)
        as_of = max(now, ledger.as_of)
        lab: dict[str, Any] | None = None
        if band is not None:
            p95 = band.max_drawdown.p95
            lab = {
                "sharpe_oos": band.sharpe_oos,
                "max_drawdown_p95": p95,
                "max_drawdown_p99": band.max_drawdown.p99,
                "drawdown_vs_p95": metrics["drawdown"] / p95 if p95 > 0 else None,
            }
        return {
            "source": "reference_ledger",
            "updated_at": as_of.isoformat(),
            "state": tracked.record.state.value,
            **metrics,
            f"sharpe_last_{window}": _finite(sharpe_recent),
            "last_signal_at": ledger.last_signal_at.isoformat() if ledger.last_signal_at else None,
            "lab": lab,
            "shadow": shadow_status(tracked, as_of, self.min_shadow_days),
            "ledger": ledger.to_state(),
        }

    # --- internos ------------------------------------------------------------------------

    def _on_trade_closed(self, tracked: Tracked, trade: ReferenceTrade) -> None:
        tracked.pending_trades.append(
            trade_row(tracked.record.id, tracked.ledger.started_at, tracked.record.state, trade)
        )
        log.info(
            "trade de referencia %s %s: pnl %.2f (%.2f%%)",
            tracked.name,
            trade.symbol,
            trade.pnl,
            trade.ret * 100,
        )
        self.check(tracked, trade.closed_at)

    def _check_silence(self, tracked: Tracked, now: datetime) -> None:
        ledger = tracked.ledger
        last = ledger.last_signal_at
        if last is None:
            waited = now - ledger.started_at
            if waited > FIRST_SIGNAL_GRACE and tracked.silence_reported_for is None:
                tracked.silence_reported_for = ledger.started_at
                self._fail(tracked, now, f"sin señales desde la entrada ({ledger.started_at})")
            return
        period = TIMEFRAME_PERIOD.get(ledger.last_timeframe or "", timedelta(days=1))
        if now - last > period * self.silence_bars and tracked.silence_reported_for != last:
            tracked.silence_reported_for = last
            self._fail(tracked, now, f"sin señales desde {last.isoformat()}")

    def _fail(self, tracked: Tracked, at: datetime, reason: str) -> None:
        log.warning("falla de '%s' en el ledger de referencia: %s", tracked.name, reason)
        tracked.health.fail(at, reason)
        tracked.dirty = True

    def _save_trades(self, tracked: Tracked) -> bool:
        if not tracked.pending_trades:
            return True
        try:
            self.store.save_trades(tracked.pending_trades)
        except ReferenceStoreError as exc:
            log.warning("no se pudieron guardar los trades de '%s': %s", tracked.name, exc)
            return False
        tracked.pending_trades = []
        return True

    def _snapshot(self, tracked: Tracked, day: date) -> None:
        """Foto del ledger para el punto de ``day`` (la última del día es la que queda)."""
        ledger = tracked.ledger
        tracked.dirty_days[day] = EquityPoint(
            strategy_id=tracked.record.id,
            started_at=ledger.started_at,
            day=day,
            state=tracked.record.state,
            equity=ledger.equity,
            cash=ledger.cash,
            positions_value=ledger.positions_value,
            drawdown=ledger.drawdown,
        )

    def _save_equity(self, tracked: Tracked) -> bool:
        if not tracked.dirty_days:
            return True
        points = [tracked.dirty_days[d] for d in sorted(tracked.dirty_days)]
        try:
            self.store.upsert_equity(points)
        except ReferenceStoreError as exc:
            log.warning("no se pudo guardar la equity de '%s': %s", tracked.name, exc)
            return False
        tracked.dirty_days = {}
        return True

    def _refresh(self, tracked: Tracked) -> None:
        try:
            tracked.record = self.registry.get(tracked.name)
        except RegistryError as exc:
            log.warning("no se pudo releer '%s': %s", tracked.name, exc)

    def _shadow_entry(self, record: StrategyRecord) -> datetime:
        """Cuándo entró a shadow por última vez (desde el historial)."""
        if record.state is S.SHADOW:
            return record.state_changed_at
        try:
            history = self.registry.history(record.name)
        except RegistryError as exc:
            log.warning("no se pudo leer el historial de '%s': %s", record.name, exc)
            history = []
        entries = [t.created_at for t in history if t.to_state is S.SHADOW]
        return max(entries) if entries else record.state_changed_at

    def _new(self, record: StrategyRecord, started_at: datetime) -> Tracked:
        log.info("ledger de referencia nuevo para '%s' desde %s", record.name, started_at)
        ledger = ReferenceLedger(
            record.name,
            started_at,
            capital=self.capital,
            fee=self.fee,
            symbols=self.symbols,
            sizing=self.sizing.get(record.name),
        )
        return Tracked(record=record, ledger=ledger)

    def _load(self, record: StrategyRecord) -> Tracked:
        """Retoma el ledger guardado en ``live_metrics`` o empieza uno nuevo."""
        started_at = self._shadow_entry(record)
        saved = record.live_metrics.get("ledger") if record.live_metrics else None
        if isinstance(saved, Mapping):
            try:
                ledger = ReferenceLedger.from_state(record.name, saved)
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("ledger de '%s' ilegible (%s); se reinicia", record.name, exc)
            else:
                if ledger.started_at >= started_at:
                    health = ShadowHealth.from_dict(record.live_metrics.get("shadow"))
                    return Tracked(record=record, ledger=ledger, health=health, dirty=False)
        return self._new(record, started_at)


def _finite(value: float | None) -> float | None:
    if value is None:
        return None
    return value if abs(value) != float("inf") else (1e9 if value > 0 else -1e9)
