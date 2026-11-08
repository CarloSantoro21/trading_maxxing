"""Streams de Redis del orquestador y sus versiones en memoria (backtest y pruebas).

- `tm:signals`: lo escribe la capa compartida y lo leen cada BotRunner y el ReferenceService.
  El evento y su lectura y escritura viven en un solo módulo, :mod:`tm_agents.signals`.
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

from dataclasses import dataclass, field
from typing import Any, Protocol

from tm_shared.contracts import DecisionEnvelope

DECISIONS_STREAM = "tm:decisions"
SHADOW_STREAM = "tm:shadow"
COMMANDS_STREAM = "tm:commands"
DEFAULT_MAXLEN = 10_000


# --- escritura ---------------------------------------------------------------------------
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
    """`XADD <stream> MAXLEN ~ n * data <json>` para sobres de `tm:decisions` y `tm:shadow`."""

    def __init__(self, client: Any, stream: str, *, maxlen: int = DEFAULT_MAXLEN) -> None:
        self._client = client
        self.stream = stream
        self._maxlen = maxlen

    def publish(self, item: DecisionEnvelope) -> str:
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


def _to_str(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def decode_fields(fields: dict[Any, Any]) -> dict[str, str]:
    """Campos de Redis (bytes o str) -> `dict[str, str]`."""
    return {_to_str(k): _to_str(v) for k, v in fields.items()}


__all__ = [
    "COMMANDS_STREAM",
    "DECISIONS_STREAM",
    "DEFAULT_MAXLEN",
    "SHADOW_STREAM",
    "DecisionSink",
    "MemorySink",
    "RedisStreamSink",
    "StreamReader",
    "decode_fields",
]
