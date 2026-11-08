"""Publicación de comandos y jobs en Redis Streams.

* `tm:commands`: órdenes de control de bots (`start`, `stop`, `kill`). El engine las lee
  con un consumer group; `kill` es el paro de emergencia: cierra las posiciones y detiene
  el bot.
* `tm:backtests`: jobs de backtest encolados (la fila en `backtests` queda `queued`).

Cada entrada es un mapa plano de strings; `payload` va como JSON.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from tm_api.settings import Settings


class CommandType(StrEnum):
    START = "start"
    STOP = "stop"
    KILL = "kill"


class StreamClient(Protocol):
    """Subconjunto de `redis.asyncio.Redis` que usa la API."""

    async def xadd(self, name: str, fields: dict[str, Any], **kwargs: Any) -> Any: ...


class CommandPublishError(Exception):
    """No se pudo escribir en Redis."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _decode(message_id: Any) -> str:
    return message_id.decode() if isinstance(message_id, bytes) else str(message_id)


class CommandBus:
    """Escribe comandos y jobs en los streams configurados."""

    def __init__(self, redis: StreamClient, settings: Settings) -> None:
        self._redis = redis
        self._settings = settings

    async def _xadd(self, stream: str, fields: dict[str, str]) -> str:
        try:
            message_id = await self._redis.xadd(
                stream, fields, maxlen=self._settings.stream_maxlen, approximate=True
            )
        except Exception as exc:  # cualquier error de conexión o de Redis
            raise CommandPublishError(str(exc)) from exc
        return _decode(message_id)

    async def publish_command(
        self,
        command: CommandType,
        *,
        bot_id: UUID,
        user_id: UUID,
        reason: str = "",
        payload: dict[str, Any] | None = None,
    ) -> str:
        fields = {
            "type": command.value,
            "bot_id": str(bot_id),
            "user_id": str(user_id),
            "reason": reason,
            "issued_at": _now(),
            "payload": json.dumps(payload or {}, default=str),
        }
        return await self._xadd(self._settings.commands_stream, fields)

    async def publish_backtest(
        self, *, backtest_id: UUID, profile_id: UUID, user_id: UUID, config: dict[str, Any]
    ) -> str:
        fields = {
            "type": "backtest",
            "backtest_id": str(backtest_id),
            "profile_id": str(profile_id),
            "user_id": str(user_id),
            "issued_at": _now(),
            "config": json.dumps(config, default=str),
        }
        return await self._xadd(self._settings.backtests_stream, fields)
