"""Fuente de señales del ledger de referencia (stream de Redis ``tm:signals``).

Las señales de cada estrategia se calculan una sola vez para todos (capa compartida,
propuesta, sección 3.4) y se publican en el stream ``tm:signals``. El orquestador (#19)
todavía no existe en ``develop``, así que aquí se fija el formato del sobre y la interfaz
:class:`SignalSource`; el orquestador solo tiene que llamar :func:`publish_signal`.

Formato de cada entrada del stream (un solo campo ``data`` con JSON)::

    XADD tm:signals * data '{"id": "...", "strategy": "trend", "close": 64250.5,
                             "bar_close_at": "2026-11-07T00:00:00Z", "signal": {...Signal}}'

- ``id``: identificador opcional (idempotencia y trazabilidad).
- ``strategy``: estrategia que la emitió; si falta se usa ``signal.source``.
- ``close``: cierre de la vela que generó la señal; es el precio del fill simulado. Si falta,
  se toma ``signal.metadata["last_close"]``.
- ``bar_close_at``: cierre de la vela (por defecto ``signal.generated_at``).
- ``signal``: el :class:`~tm_shared.contracts.Signal` serializado con ``model_dump(mode="json")``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from tm_shared.contracts import Signal

log = logging.getLogger(__name__)

SIGNALS_STREAM = "tm:signals"
#: Llave de Redis con el último id del stream que procesó el ledger de referencia.
REFERENCE_CHECKPOINT = "tm:reference:last_signal_id"


class SignalEnvelope(BaseModel):
    """Señal de una estrategia con el precio de cierre de su vela."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str | None = None
    strategy: str = Field(default="", pattern=r"^([a-z][a-z0-9_]{0,63})?$")
    signal: Signal
    close: float | None = Field(default=None, gt=0)
    bar_close_at: datetime | None = None

    @field_validator("bar_close_at")
    @classmethod
    def _tz_aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("bar_close_at debe tener zona horaria (UTC)")
        return v.astimezone(UTC) if v is not None else None

    @model_validator(mode="after")
    def _defaults(self) -> SignalEnvelope:
        if not self.strategy:
            object.__setattr__(self, "strategy", self.signal.source.lower())
        if self.close is None:
            last = self.signal.metadata.get("last_close")
            if isinstance(last, int | float) and last > 0:
                object.__setattr__(self, "close", float(last))
        if self.bar_close_at is None:
            object.__setattr__(self, "bar_close_at", self.signal.generated_at)
        return self

    @property
    def symbol(self) -> str:
        return self.signal.symbol

    @property
    def ts(self) -> datetime:
        """Instante del fill simulado: el cierre de la vela."""
        assert self.bar_close_at is not None
        return self.bar_close_at

    def to_json(self) -> str:
        return self.model_dump_json()


class SignalSource(Protocol):
    """Algo que entrega las señales nuevas cuando el ledger pregunta."""

    def poll(self) -> list[SignalEnvelope]: ...


class InMemorySignalSource:
    """Cola de señales en memoria (pruebas, backtests y uso local sin Redis)."""

    def __init__(self, envelopes: Iterable[SignalEnvelope] = ()) -> None:
        self._pending = list(envelopes)

    def push(self, envelope: SignalEnvelope) -> None:
        self._pending.append(envelope)

    def poll(self) -> list[SignalEnvelope]:
        out, self._pending = self._pending, []
        return out

    def __len__(self) -> int:
        return len(self._pending)


def parse_entry(fields: dict[Any, Any]) -> SignalEnvelope | None:
    """Convierte una entrada del stream en sobre; ``None`` (y un log) si viene malformada."""
    raw = fields.get("data", fields.get(b"data"))
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        return SignalEnvelope.model_validate(json.loads(raw or "null"))
    except (ValidationError, json.JSONDecodeError, TypeError) as exc:
        log.warning("señal inválida en %s: %s", SIGNALS_STREAM, exc)
        return None


def publish_signal(client: Any, envelope: SignalEnvelope, stream: str = SIGNALS_STREAM) -> str:
    """Publica la señal en el stream (``XADD``) y devuelve el id de la entrada."""
    entry_id = client.xadd(stream, {"data": envelope.to_json()})
    return _to_str(entry_id)


class RedisStreamSignalSource:
    """Lee señales nuevas del stream ``tm:signals`` sin bloquear.

    Empieza en ``last_id``; con ``"$"`` (por defecto) solo lee lo que llegue después de
    arrancar. Con ``checkpoint_key`` retoma desde el último id confirmado con :meth:`commit`
    (se guarda en esa llave de Redis), así un reinicio no pierde señales.
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
        self._stream = stream
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

    def poll(self) -> list[SignalEnvelope]:
        response = self._client.xread({self._stream: self.last_id}, count=self._batch)
        out: list[SignalEnvelope] = []
        for _stream, entries in response or []:
            for entry_id, fields in entries:
                self.last_id = _to_str(entry_id)
                envelope = parse_entry(fields)
                if envelope is None:
                    self.invalid += 1
                else:
                    out.append(envelope)
        return out

    def commit(self) -> None:
        """Confirma lo leído hasta ahora (después de guardar el ledger)."""
        if self._checkpoint_key:
            self._client.set(self._checkpoint_key, self.last_id)


def _to_str(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)
