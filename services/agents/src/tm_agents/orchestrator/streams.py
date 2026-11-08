"""Streams de Redis del orquestador y sus versiones en memoria (backtest y pruebas).

- `tm:signals`: lo escribe la capa compartida y lo lee cada BotRunner. `SignalEvent`.
- `tm:decisions`: lo escribe el BotRunner y lo lee el engine (`AgenticStrategy`).
  `DecisionEnvelope` con `bot_id`, `sleeve` y `target_quantity`.
- `tm:shadow`: lo escribe el BotRunner; dashboard y auditoría. `DecisionEnvelope` con
  `shadow=true`; nunca va al engine.
- `tm:commands`: lo escribe la API (#21) y lo lee el BotSupervisor. Campos planos `type`,
  `bot_id`, `user_id`, `reason`, `issued_at` y `payload`.

Salvo `tm:commands`, cada entrada tiene un solo campo `data` con el JSON del modelo.

Todos los lectores empiezan en `$` (lo que llegue después de arrancar) salvo que se indique
otro `last_id`, y leen sin bloquear.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from tm_shared.contracts import DecisionEnvelope

from tm_agents.orchestrator.events import SignalEvent, parse_event

SIGNALS_STREAM = "tm:signals"
DECISIONS_STREAM = "tm:decisions"
SHADOW_STREAM = "tm:shadow"
COMMANDS_STREAM = "tm:commands"
DEFAULT_MAXLEN = 10_000


# --- escritura ---------------------------------------------------------------------------
class EventSink(Protocol):
    def publish(self, event: SignalEvent) -> str | None: ...


class DecisionSink(Protocol):
    def publish(self, envelope: DecisionEnvelope) -> str | None: ...


@dataclass
class MemorySink[T]:
    """Guarda lo publicado en una lista (backtest y pruebas)."""

    items: list[T] = field(default_factory=list)

    def publish(self, item: T) -> str:
        self.items.append(item)
        return str(len(self.items))


class RedisStreamSink:
    """`XADD <stream> MAXLEN ~ n * data <json>` para eventos o sobres de decisión."""

    def __init__(self, client: Any, stream: str, *, maxlen: int = DEFAULT_MAXLEN) -> None:
        self._client = client
        self.stream = stream
        self._maxlen = maxlen

    def publish(self, item: SignalEvent | DecisionEnvelope) -> str:
        entry = self._client.xadd(
            self.stream, {"data": item.to_json()}, maxlen=self._maxlen, approximate=True
        )
        return _to_str(entry)


# --- lectura -----------------------------------------------------------------------------
class StreamReader:
    """Lee entradas nuevas de un stream sin bloquear (`XREAD` desde el último id visto)."""

    def __init__(self, client: Any, stream: str, *, last_id: str = "$", batch: int = 500):
        self._client = client
        self.stream = stream
        self._batch = batch
        self.last_id = last_id
        if last_id == "$":
            # Se fija el id actual para no perder lo que llegue entre el arranque y el poll.
            info = client.xinfo_stream(stream) if client.exists(stream) else None
            self.last_id = _to_str(info["last-generated-id"]) if info else "0-0"

    def read(self) -> list[tuple[str, dict[Any, Any]]]:
        response = self._client.xread({self.stream: self.last_id}, count=self._batch)
        out: list[tuple[str, dict[Any, Any]]] = []
        for _stream, entries in response or []:
            for entry_id, fields in entries:
                self.last_id = _to_str(entry_id)
                out.append((self.last_id, fields))
        return out


class SignalSource(Protocol):
    """De dónde saca un BotRunner los eventos: Redis en vivo o una lista en backtest."""

    def poll(self, now: datetime | None = None) -> list[SignalEvent]: ...


class RedisSignalSource:
    def __init__(self, client: Any, stream: str = SIGNALS_STREAM, *, last_id: str = "$"):
        self._reader = StreamReader(client, stream, last_id=last_id)
        self.invalid = 0

    def poll(self, now: datetime | None = None) -> list[SignalEvent]:
        out: list[SignalEvent] = []
        for _id, fields in self._reader.read():
            event = parse_event(fields)
            if event is None:
                self.invalid += 1
                continue
            out.append(event)
        return out


class ListSignalSource:
    """Eventos fijos que se liberan cuando `now >= bar_close` (en orden)."""

    def __init__(self, events: Iterable[SignalEvent]) -> None:
        self._pending = sorted(events, key=lambda e: (e.bar_close, e.strategy, e.symbol))

    def poll(self, now: datetime | None = None) -> list[SignalEvent]:
        if now is None:
            ready, self._pending = self._pending, []
            return ready
        ready = [e for e in self._pending if e.bar_close <= now]
        self._pending = self._pending[len(ready) :]
        return ready


def _to_str(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def decode_fields(fields: dict[Any, Any]) -> dict[str, str]:
    """Campos de Redis (bytes o str) -> `dict[str, str]`."""
    return {_to_str(k): _to_str(v) for k, v in fields.items()}


__all__ = [
    "COMMANDS_STREAM",
    "DECISIONS_STREAM",
    "SHADOW_STREAM",
    "SIGNALS_STREAM",
    "DecisionSink",
    "EventSink",
    "ListSignalSource",
    "MemorySink",
    "RedisSignalSource",
    "RedisStreamSink",
    "SignalSource",
    "StreamReader",
    "decode_fields",
]
