"""Fuentes de decisiones para AgenticStrategy.

El orquestador publica cada `TradeDecision` en el stream de Redis `tm:decisions` dentro de un
sobre (`DecisionEnvelope`) que dice a qué sleeve pertenece. Formato de cada entrada del stream
(un solo campo `data` con JSON):

    XADD tm:decisions * data '{"id": "...", "sleeve": "trend", "decision": {...TradeDecision}}'

- `id`: identificador opcional de la decisión (para idempotencia y trazabilidad).
- `sleeve`: nombre de la estrategia dueña del capital (`trend`, `intraday_momentum`, `kronos`).
  Si falta, se usa el `source` de la primera señal del rationale o `default`.
- `decision`: el `TradeDecision` de `tm_shared` serializado con `model_dump(mode="json")`.

En backtest se usa `ScheduledDecisionSource`, que suelta cada decisión cuando el reloj del
motor alcanza su `decided_at`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from tm_shared.contracts import TradeDecision

from tm_engine.sleeves import DEFAULT_SLEEVE

log = logging.getLogger(__name__)

DECISIONS_STREAM = "tm:decisions"


class DecisionEnvelope(BaseModel):
    """Decisión del orquestador con el sleeve al que se atribuye."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str | None = None
    sleeve: str = Field(default="", pattern=r"^[a-z0-9_\-]{0,64}$")
    decision: TradeDecision

    @model_validator(mode="after")
    def _default_sleeve(self) -> DecisionEnvelope:
        if not self.sleeve:
            signals = self.decision.rationale.signals
            name = signals[0].source.lower() if signals else DEFAULT_SLEEVE
            object.__setattr__(self, "sleeve", name)
        return self

    def to_json(self) -> str:
        return self.model_dump_json()


class DecisionSource(Protocol):
    """Algo que entrega decisiones pendientes cuando la estrategia pregunta."""

    def poll(self, now: datetime) -> list[DecisionEnvelope]: ...


class ScheduledDecisionSource:
    """Decisiones fijas que se liberan cuando `now >= decided_at` (para backtests)."""

    def __init__(self, envelopes: Iterable[DecisionEnvelope]) -> None:
        self._pending = sorted(envelopes, key=lambda e: e.decision.decided_at)

    def poll(self, now: datetime) -> list[DecisionEnvelope]:
        ready = [e for e in self._pending if e.decision.decided_at <= now]
        self._pending = self._pending[len(ready) :]
        return ready

    @property
    def pending(self) -> int:
        return len(self._pending)


def parse_entry(fields: dict[Any, Any]) -> DecisionEnvelope | None:
    """Convierte una entrada del stream en sobre; `None` (y un log) si viene malformada."""
    raw = fields.get("data", fields.get(b"data"))
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        return DecisionEnvelope.model_validate(json.loads(raw or "null"))
    except (ValidationError, json.JSONDecodeError, TypeError) as exc:
        log.warning("decisión inválida en %s: %s", DECISIONS_STREAM, exc)
        return None


class RedisStreamDecisionSource:
    """Lee decisiones nuevas del stream `tm:decisions` sin bloquear.

    Empieza en `$` (solo lo que llegue después de arrancar) salvo que se indique `last_id`.
    """

    def __init__(
        self,
        client: Any,
        stream: str = DECISIONS_STREAM,
        *,
        last_id: str = "$",
        batch: int = 100,
    ) -> None:
        self._client = client
        self._stream = stream
        self._batch = batch
        self.last_id = last_id
        if last_id == "$":
            # Fijamos el id actual para no perder lo que llegue entre arranque y primer poll.
            info = client.xinfo_stream(stream) if client.exists(stream) else None
            self.last_id = _to_str(info["last-generated-id"]) if info else "0-0"

    def poll(self, now: datetime | None = None) -> list[DecisionEnvelope]:
        response = self._client.xread({self._stream: self.last_id}, count=self._batch)
        out: list[DecisionEnvelope] = []
        for _stream, entries in response or []:
            for entry_id, fields in entries:
                self.last_id = _to_str(entry_id)
                envelope = parse_entry(fields)
                if envelope is not None:
                    out.append(envelope)
        return out


def publish_decision(
    client: Any, envelope: DecisionEnvelope, stream: str = DECISIONS_STREAM
) -> str:
    """Publica un sobre en el stream (lo usa el orquestador y las pruebas)."""
    return _to_str(client.xadd(stream, {"data": envelope.to_json()}))


def _to_str(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
