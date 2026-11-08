"""Stream ``tm:signals``: la única definición del evento y de cómo se publica y se consume.

La capa compartida del orquestador (#19, :mod:`tm_agents.orchestrator.shared`) calcula las
señales una vez para todos y las publica aquí; las leen, cada uno con su propio cursor, los
BotRunner (:mod:`tm_agents.orchestrator`) y el ledger de referencia
(:class:`tm_agents.reference.ReferenceService`, #57). Ambos usan este módulo y nada más.

Formato del stream (un solo campo ``data`` con JSON, igual que ``tm:decisions``)::

    XADD tm:signals MAXLEN ~ 10000 * data '<SignalEvent en JSON>'

Un :class:`SignalEvent` por estrategia (activa, en observación o en shadow) y símbolo en cada
cierre de vela del timeframe de la estrategia::

    {
      "id": "trend:BTCUSDT:2024-03-01T00:00:00Z",   # estrategia:símbolo:cierre (idempotencia)
      "strategy": "trend",
      "state": "active",                            # active | probation | shadow
      "symbol": "BTCUSDT",
      "bar_close": "2024-03-01T00:00:00Z",          # cierre de la vela que produjo la señal
      "price": "61234.5",                           # cierre de esa vela (tamaño y fill simulado)
      "signal": {...Signal},                        # dirección, confianza, metadata
      "veto": null | {"scope": "BTC", "impact": "high", "reason": "...", "expires_at": "..."},
      "sim": null | {...SimResult},                 # SimulationGate (solo si la señal es long)
      "sim_error": null | "historia insuficiente ...",
      "stop_loss": "3061.72", "take_profit": "489876.0",
      "hard_stop": false,                           # true = stop real (Kronos); false = catástrofe
      "catalog": [{...StrategySnapshot}, ...]       # estado y estadísticas de todas las vivas
    }

`catalog` viaja en cada evento para que cada bot calcule sus pesos (PortfolioAllocator) y su
exposición por nivel sin volver a leer el registro: el evento basta para reproducir la
decisión del bot. El ledger de referencia solo usa ``strategy``, ``symbol``, ``bar_close``,
``price`` y ``signal``: llena al cierre de la vela a tamaño completo, sin vetos ni gate,
porque mide a la estrategia y no a un usuario.

Publicar: :func:`publish_signal` (o :class:`RedisSignalSink`, el sink de la capa compartida).
Consumir: :class:`RedisSignalSource` (con ``checkpoint_key`` retoma tras un reinicio) o
:class:`ListSignalSource` (backtest y pruebas); los dos cumplen :class:`SignalSource`.
Cada consumidor lee con ``XREAD`` desde su propio último id, así que todos ven todas las
entradas.
"""

from __future__ import annotations

import bisect
import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from tm_shared.contracts import Horizon, Signal, SimResult

from tm_agents.news.models import Veto
from tm_agents.registry import StrategyRecord
from tm_agents.strategy_stats import StrategyState, StrategyStats

log = logging.getLogger(__name__)

SIGNALS_STREAM = "tm:signals"
#: Largo aproximado que se conserva del stream (``XADD ... MAXLEN ~``).
SIGNALS_MAXLEN = 10_000

#: Estados que reciben eventos: los que operan capital y los que corren en shadow.
LIVE_STATES: tuple[StrategyState, ...] = (
    StrategyState.ACTIVE,
    StrategyState.PROBATION,
    StrategyState.SHADOW,
)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class StrategySnapshot(_Frozen):
    """Lo que un bot necesita saber de una estrategia para dimensionar su sleeve.

    Sale de la fila de ``strategies`` (estado) y de su ``lab_report`` (lo que midió el
    laboratorio). Una estrategia sin reporte llega con volatilidad 0 y el PortfolioAllocator
    no le da capital.
    """

    name: str
    state: StrategyState
    volatility: float = Field(ge=0.0)
    oos_sharpe: float
    horizon: Horizon | None = None
    max_drawdown_p95: float | None = Field(default=None, ge=0.0, le=1.0)
    #: Curva de sensibilidad del laboratorio: pares ``(exposición, drawdown p95)``. Sale del
    #: reporte (``exposure_sensitivity``) o de la curva medida para su variante.
    sensitivity: tuple[tuple[float, float], ...] | None = None

    @classmethod
    def from_record(cls, record: StrategyRecord) -> StrategySnapshot:
        # Import diferido: tm_agents.orchestrator importa este módulo.
        from tm_agents.orchestrator.exposure import KNOWN_SENSITIVITY

        report: Mapping[str, Any] = record.lab_report or {}
        aggregate = report.get("aggregate") or {}
        mc = (report.get("montecarlo") or {}).get("max_drawdown") or {}
        # Mismos formatos que lee el LifecycleMonitor (``DrawdownBand.from_lab_report``):
        # ``montecarlo.max_drawdown.p95`` o el plano ``max_drawdown_p95``.
        p95 = mc.get("p95", report.get("max_drawdown_p95"))
        horizon = report.get("horizon")
        # La curva del reporte; si no trae, la medida para su variante (config.strategy).
        lab_name = (report.get("config") or {}).get("strategy") or record.name
        sensitivity = report.get("exposure_sensitivity") or KNOWN_SENSITIVITY.get(lab_name)
        return cls(
            name=record.name,
            state=record.state,
            volatility=_finite(aggregate.get("volatility"), 0.0),
            oos_sharpe=_finite(aggregate.get("sharpe"), 0.0),
            horizon=Horizon(horizon) if horizon else None,
            max_drawdown_p95=_finite(p95, None),
            sensitivity=tuple((float(k), float(d)) for k, d in sensitivity)
            if sensitivity
            else None,
        )

    def to_stats(self, *, state: StrategyState | None = None) -> StrategyStats:
        return StrategyStats(
            name=self.name,
            state=state or self.state,
            volatility=self.volatility,
            oos_sharpe=self.oos_sharpe,
            horizon=self.horizon,
            max_drawdown_p95=self.max_drawdown_p95,
        )


class VetoInfo(_Frozen):
    """Veto de noticias vigente para el símbolo del evento."""

    scope: str
    impact: str
    reason: str
    expires_at: datetime

    @classmethod
    def from_veto(cls, veto: Veto) -> VetoInfo:
        return cls(
            scope=veto.scope,
            impact=str(veto.impact.value if hasattr(veto.impact, "value") else veto.impact),
            reason=veto.reason,
            expires_at=veto.expires_at,
        )


class SignalEvent(_Frozen):
    """Una señal ya evaluada por la capa compartida (ver el formato en el docstring)."""

    id: str
    #: Nombre de la estrategia en el registro (mismo patrón que ``strategies.name``).
    strategy: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    state: StrategyState
    symbol: str = Field(pattern=r"^[A-Z0-9]{5,20}$")
    bar_close: datetime
    price: Decimal = Field(gt=0)
    signal: Signal
    veto: VetoInfo | None = None
    sim: SimResult | None = None
    sim_error: str | None = None
    stop_loss: Decimal = Field(gt=0)
    take_profit: Decimal = Field(gt=0)
    hard_stop: bool = False
    catalog: tuple[StrategySnapshot, ...] = ()

    @field_validator("bar_close")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("bar_close debe tener zona horaria (UTC)")
        return v.astimezone(UTC)

    @classmethod
    def from_signal(
        cls,
        signal: Signal,
        price: Decimal | float | str,
        *,
        strategy: str | None = None,
        state: StrategyState = StrategyState.ACTIVE,
        bar_close: datetime | None = None,
        catalog: Sequence[StrategySnapshot] = (),
    ) -> SignalEvent:
        """Evento sin veto ni SimulationGate a partir de una señal y el cierre de su vela.

        Para backtests, pruebas y productores fuera de la capa compartida. ``strategy`` es
        ``signal.source`` en minúsculas y ``bar_close``, ``signal.generated_at``. Los niveles
        son de catástrofe (``hard_stop=False``): la mitad y ocho veces el precio.
        """
        name = strategy or signal.source.lower()
        when = bar_close or signal.generated_at
        p = Decimal(str(price))
        return cls(
            id=event_id(name, signal.symbol, when),
            strategy=name,
            state=state,
            symbol=signal.symbol,
            bar_close=when,
            price=p,
            signal=signal,
            stop_loss=p / 2,
            take_profit=p * 8,
            catalog=tuple(catalog),
        )

    @property
    def is_shadow(self) -> bool:
        return self.state is StrategyState.SHADOW

    def snapshot(self, name: str | None = None) -> StrategySnapshot | None:
        name = name or self.strategy
        return next((s for s in self.catalog if s.name == name), None)

    def to_json(self) -> str:
        return self.model_dump_json()


def event_id(strategy: str, symbol: str, bar_close: datetime) -> str:
    ts = bar_close.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"{strategy}:{symbol}:{ts}"


def parse_signal(fields: Mapping[Any, Any]) -> SignalEvent | None:
    """Entrada del stream -> evento; ``None`` (y un log) si viene malformada."""
    raw = fields.get("data", fields.get(b"data"))
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        return SignalEvent.model_validate(json.loads(raw or "null"))
    except (ValidationError, json.JSONDecodeError, TypeError) as exc:
        log.warning("señal inválida en %s: %s", SIGNALS_STREAM, exc)
        return None


def publish_signal(
    client: Any,
    event: SignalEvent,
    *,
    stream: str = SIGNALS_STREAM,
    maxlen: int | None = SIGNALS_MAXLEN,
) -> str:
    """``XADD tm:signals MAXLEN ~ n * data <json>``; devuelve el id de la entrada."""
    kwargs: dict[str, Any] = {"maxlen": maxlen, "approximate": True} if maxlen else {}
    return _to_str(client.xadd(stream, {"data": event.to_json()}, **kwargs))


class SignalSink(Protocol):
    """Dónde publica la capa compartida: :class:`RedisSignalSink` o una lista en backtest."""

    def publish(self, event: SignalEvent) -> str | None: ...


class RedisSignalSink:
    """Sink de la capa compartida: publica cada evento con :func:`publish_signal`."""

    def __init__(
        self, client: Any, stream: str = SIGNALS_STREAM, *, maxlen: int | None = SIGNALS_MAXLEN
    ) -> None:
        self._client = client
        self.stream = stream
        self._maxlen = maxlen

    def publish(self, event: SignalEvent) -> str:
        return publish_signal(self._client, event, stream=self.stream, maxlen=self._maxlen)


class SignalSource(Protocol):
    """De dónde salen los eventos: Redis en vivo o una lista en backtest y pruebas.

    ``now`` lo usan las fuentes de backtest para liberar solo las velas ya cerradas; las de
    Redis lo ignoran.
    """

    def poll(self, now: datetime | None = None) -> list[SignalEvent]: ...


class RedisSignalSource:
    """Lee eventos nuevos de ``tm:signals`` sin bloquear (``XREAD`` desde el último id).

    Empieza en ``last_id``; con ``"$"`` (por defecto) solo lee lo que llegue después de
    arrancar (se fija el id actual para no perder lo que llegue antes del primer ``poll``).
    Con ``checkpoint_key`` retoma desde el último id confirmado con :meth:`commit` (se guarda
    en esa llave de Redis), así un reinicio no pierde señales. Las entradas malformadas se
    saltan y se cuentan en ``invalid``.
    """

    def __init__(
        self,
        client: Any,
        stream: str = SIGNALS_STREAM,
        *,
        last_id: str = "$",
        batch: int = 500,
        checkpoint_key: str | None = None,
    ) -> None:
        self._client = client
        self.stream = stream
        self._batch = batch
        self._checkpoint_key = checkpoint_key
        self.invalid = 0
        saved = client.get(checkpoint_key) if checkpoint_key else None
        if saved:
            last_id = _to_str(saved)
        self.last_id = last_id
        if last_id == "$":
            info = client.xinfo_stream(stream) if client.exists(stream) else None
            self.last_id = _to_str(info["last-generated-id"]) if info else "0-0"

    def poll(self, now: datetime | None = None) -> list[SignalEvent]:
        response = self._client.xread({self.stream: self.last_id}, count=self._batch)
        out: list[SignalEvent] = []
        for _stream, entries in response or []:
            for entry_id, fields in entries:
                self.last_id = _to_str(entry_id)
                event = parse_signal(fields)
                if event is None:
                    self.invalid += 1
                else:
                    out.append(event)
        return out

    def commit(self) -> None:
        """Confirma lo leído hasta ahora (después de guardar lo que dependa de ello)."""
        if self._checkpoint_key:
            self._client.set(self._checkpoint_key, self.last_id)


class ListSignalSource:
    """Eventos en memoria que se liberan en orden cuando ``now >= bar_close``.

    Con ``now=None`` entrega todo lo pendiente. :meth:`push` agrega eventos (pruebas y uso
    local sin Redis).
    """

    def __init__(self, events: Iterable[SignalEvent] = ()) -> None:
        self._pending = sorted(events, key=_order)

    def push(self, event: SignalEvent) -> None:
        bisect.insort(self._pending, event, key=_order)

    def poll(self, now: datetime | None = None) -> list[SignalEvent]:
        if now is None:
            ready, self._pending = self._pending, []
            return ready
        ready = [e for e in self._pending if e.bar_close <= now]
        self._pending = self._pending[len(ready) :]
        return ready

    def __len__(self) -> int:
        return len(self._pending)


def _order(event: SignalEvent) -> tuple[datetime, str, str]:
    return (event.bar_close, event.strategy, event.symbol)


def _to_str(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def catalog_from_records(records: Sequence[StrategyRecord]) -> tuple[StrategySnapshot, ...]:
    return tuple(StrategySnapshot.from_record(r) for r in records)


def _finite(value: Any, default: float | None) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out == out and abs(out) != float("inf") else default


__all__ = [
    "LIVE_STATES",
    "SIGNALS_MAXLEN",
    "SIGNALS_STREAM",
    "ListSignalSource",
    "RedisSignalSink",
    "RedisSignalSource",
    "SignalEvent",
    "SignalSink",
    "SignalSource",
    "StrategySnapshot",
    "VetoInfo",
    "catalog_from_records",
    "event_id",
    "parse_signal",
    "publish_signal",
]
