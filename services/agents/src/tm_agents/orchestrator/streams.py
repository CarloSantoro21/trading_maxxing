"""Streams de Redis del orquestador y sus versiones en memoria (backtest y pruebas).

- `tm:signals`: lo escribe la capa compartida y lo leen cada BotRunner y el ReferenceService.
  El evento y su lectura y escritura viven en un solo módulo, :mod:`tm_agents.signals`.
- `tm:decisions`: lo escribe el BotRunner y lo lee el engine (`AgenticStrategy`).
  `DecisionEnvelope` con `bot_id`, `sleeve` y `target_quantity`.
- `tm:shadow`: lo escribe el BotRunner; dashboard y auditoría. `DecisionEnvelope` con
  `shadow=true`; nunca va al engine.

Cada entrada tiene un solo campo `data` con el JSON del modelo. Los comandos de bots ya no
pasan por Redis: viven en la cola `commands` de D1 y el BotSupervisor los lee con
`GET /internal/commands` (:class:`~tm_agents.orchestrator.supervisor.WorkerApiCommandListener`).

Para auditoría y para el tablero, :class:`WorkerApiDecisionSink` copia cada sobre a
`POST /internal/decisions` (`kind` = `decision` o `shadow`); :class:`FanoutSink` publica en
Redis y en la API a la vez.

Todos los lectores empiezan en `$` (lo que llegue después de arrancar) salvo que se indique
otro `last_id`, y leen sin bloquear.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from tm_shared.api_client import WorkerApiClient, WorkerApiError
from tm_shared.contracts import DecisionEnvelope

log = logging.getLogger(__name__)

DECISIONS_STREAM = "tm:decisions"
SHADOW_STREAM = "tm:shadow"
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


class WorkerApiDecisionSink:
    """Copia cada sobre a ``POST /internal/decisions`` (auditoría y ``GET /api/decisions``).

    Nunca lanza ni reintenta: la auditoría no puede frenar al orquestador. ``kind`` es
    ``decision`` para las que van al motor y ``shadow`` para las de estrategias en shadow.
    """

    def __init__(self, client: WorkerApiClient, *, kind: str = "decision") -> None:
        self.client = client
        self.kind = kind
        self.failed = 0

    def publish(self, item: DecisionEnvelope) -> str | None:
        decision = item.decision
        payload = item.model_dump(mode="json")
        row: dict[str, Any] = {
            "strategy": item.sleeve or None,
            "symbol": decision.symbol,
            "kind": self.kind,
            "payload": payload,
            "decided_at": decision.decided_at.isoformat(),
        }
        if item.bot_id and _is_uuid(item.bot_id):
            row["bot_id"] = item.bot_id
        try:
            self.client.post("/internal/decisions", {"decisions": [row]}, retries=0)
        except WorkerApiError as exc:
            self.failed += 1
            log.warning("no se pudo auditar la decisión %s: %s", item.id, exc)
            return None
        return item.id


class FanoutSink:
    """Publica en varios sinks; devuelve lo que devuelva el primero (el stream de Redis).

    Si un sink secundario falla se registra y se sigue: solo el primero puede lanzar.
    """

    def __init__(self, primary: DecisionSink, *others: DecisionSink) -> None:
        self.sinks: Sequence[DecisionSink] = (primary, *others)

    def publish(self, item: DecisionEnvelope) -> str | None:
        out = self.sinks[0].publish(item)
        for sink in self.sinks[1:]:
            try:
                sink.publish(item)
            except Exception as exc:
                log.warning("falló un sink secundario (%s): %s", type(sink).__name__, exc)
        return out


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except (TypeError, ValueError):
        return False
    return True


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
    "DECISIONS_STREAM",
    "DEFAULT_MAXLEN",
    "SHADOW_STREAM",
    "DecisionSink",
    "FanoutSink",
    "MemorySink",
    "RedisStreamSink",
    "StreamReader",
    "WorkerApiDecisionSink",
    "decode_fields",
]
