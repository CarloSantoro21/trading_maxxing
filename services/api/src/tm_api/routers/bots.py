"""Bots: alta, arranque, paro y paro de emergencia (kill switch).

Cada cambio de estado se escribe primero en la tabla `bots` (con el JWT del usuario, así
RLS garantiza que el bot es suyo) y después se publica el comando en el stream
`tm:commands` de Redis, que lee el engine.

Si Redis falla:

* en `start` se revierte el estado para no dejar un bot "running" que el engine nunca
  arrancó;
* en `stop` y `kill` el estado se conserva (es el lado seguro) y se responde 503 para que
  el usuario lo reintente: el engine también revisa el estado en la base.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Query, Response, status

from tm_api.auth import CurrentUser, User
from tm_api.commands import CommandBus, CommandPublishError, CommandType
from tm_api.db import Database, Row
from tm_api.deps import DB, Bus, get_one, not_found, redis_unavailable, resolve_profile
from tm_api.schemas import (
    Bot,
    BotCommandResult,
    BotCreate,
    BotStatus,
    BotUpdate,
    KillAllResult,
    KillRequest,
)

router = APIRouter(prefix="/bots", tags=["bots"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Token ausente o inválido"},
    404: {"description": "El bot no existe o no es tuyo"},
}
_COMMAND_ERRORS: dict[int | str, dict[str, Any]] = {
    **_ERRORS,
    409: {"description": "El bot no está en un estado que permita la acción"},
    503: {"description": "El estado se guardó pero Redis no está disponible"},
}

# Estados desde los que se permite cada transición.
_CAN_START = {BotStatus.STOPPED, BotStatus.PAUSED, BotStatus.KILLED}
_CAN_STOP = {BotStatus.RUNNING, BotStatus.PAUSED}


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def _update_bot(db: Database, user: CurrentUser, bot_id: UUID, values: Row) -> Row:
    rows = await db.update("bots", user.token, {"id": f"eq.{bot_id}"}, values)
    if not rows:
        raise not_found("Bot")
    return rows[0]


def _conflict(bot: Row, action: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"No se puede {action} un bot en estado {bot['status']}",
    )


@router.get("", response_model=list[Bot], summary="Listar mis bots", responses=_ERRORS)
async def list_bots(
    user: User,
    db: DB,
    profile_id: Annotated[UUID | None, Query(description="Filtrar por perfil")] = None,
    bot_status: Annotated[BotStatus | None, Query(alias="status", description="Estado")] = None,
) -> list[Row]:
    params = {"order": "created_at.asc"}
    if profile_id:
        params["profile_id"] = f"eq.{profile_id}"
    if bot_status:
        params["status"] = f"eq.{bot_status.value}"
    return await db.select("bots", user.token, params)


@router.post(
    "",
    response_model=Bot,
    status_code=status.HTTP_201_CREATED,
    summary="Crear un bot (queda detenido)",
    responses={401: _ERRORS[401], 404: {"description": "El perfil no existe o no es tuyo"}},
)
async def create_bot(body: BotCreate, user: User, db: DB) -> Row:
    profile = await resolve_profile(db, user, body.profile_id)
    row = body.model_dump(mode="json")
    row["profile_id"] = profile["id"]
    return await db.insert("bots", user.token, row)


@router.post(
    "/kill-all",
    response_model=KillAllResult,
    summary="Paro de emergencia de todos mis bots",
    description=(
        "Marca como `killed` todos los bots del usuario que no lo estén y publica un "
        "comando `kill` por cada uno en `tm:commands`. El engine cierra sus posiciones "
        "(vende a USDT) y los detiene."
    ),
    responses={401: _ERRORS[401], 503: _COMMAND_ERRORS[503]},
)
async def kill_all(
    user: User, db: DB, bus: Bus, body: Annotated[KillRequest | None, Body()] = None
) -> KillAllResult:
    reason = (body or KillRequest()).reason
    bots = await db.select("bots", user.token, {"status": "neq.killed", "order": "created_at.asc"})
    results: list[BotCommandResult] = []
    for bot in bots:
        results.append(await _kill(db, bus, user, UUID(str(bot["id"])), reason))
    return KillAllResult(killed=results)


@router.get("/{bot_id}", response_model=Bot, summary="Ver un bot", responses=_ERRORS)
async def get_bot(bot_id: UUID, user: User, db: DB) -> Row:
    return await get_one(db, "bots", user, bot_id, "Bot")


@router.patch(
    "/{bot_id}",
    response_model=Bot,
    summary="Renombrar o cambiar la configuración de estrategias",
    responses=_ERRORS,
)
async def update_bot(bot_id: UUID, body: BotUpdate, user: User, db: DB) -> Row:
    values = body.model_dump(mode="json", exclude_unset=True)
    return await _update_bot(db, user, bot_id, values)


@router.delete(
    "/{bot_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Borrar un bot detenido",
    responses={**_ERRORS, 409: {"description": "El bot sigue corriendo; deténlo primero"}},
)
async def delete_bot(bot_id: UUID, user: User, db: DB) -> Response:
    bot = await get_one(db, "bots", user, bot_id, "Bot")
    if bot["status"] in (BotStatus.RUNNING, BotStatus.PAUSED):
        raise _conflict(bot, "borrar")
    await db.delete("bots", user.token, {"id": f"eq.{bot_id}"})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{bot_id}/start",
    response_model=BotCommandResult,
    summary="Arrancar un bot",
    description="Pasa el bot a `running` y publica `start` en `tm:commands`.",
    responses=_COMMAND_ERRORS,
)
async def start_bot(bot_id: UUID, user: User, db: DB, bus: Bus) -> BotCommandResult:
    bot = await get_one(db, "bots", user, bot_id, "Bot")
    if bot["status"] not in _CAN_START:
        raise _conflict(bot, "arrancar")
    updated = await _update_bot(
        db,
        user,
        bot_id,
        {"status": BotStatus.RUNNING.value, "started_at": _now(), "stopped_at": None},
    )
    try:
        command_id = await bus.publish_command(
            CommandType.START,
            bot_id=bot_id,
            user_id=user.id,
            payload={"strategy_config": updated.get("strategy_config", {})},
        )
    except CommandPublishError as exc:
        # Se deshace el arranque: el engine nunca recibió la orden.
        await _update_bot(
            db,
            user,
            bot_id,
            {
                "status": bot["status"],
                "started_at": bot.get("started_at"),
                "stopped_at": bot.get("stopped_at"),
            },
        )
        raise redis_unavailable(exc, "El bot no se arrancó") from exc
    return BotCommandResult(bot=Bot.model_validate(updated), command="start", command_id=command_id)


@router.post(
    "/{bot_id}/stop",
    response_model=BotCommandResult,
    summary="Detener un bot",
    description=(
        "Pasa el bot a `stopped` y publica `stop` en `tm:commands`: deja de abrir "
        "operaciones nuevas. Para cerrar también las posiciones usa `/kill`."
    ),
    responses=_COMMAND_ERRORS,
)
async def stop_bot(bot_id: UUID, user: User, db: DB, bus: Bus) -> BotCommandResult:
    bot = await get_one(db, "bots", user, bot_id, "Bot")
    if bot["status"] not in _CAN_STOP:
        raise _conflict(bot, "detener")
    updated = await _update_bot(
        db, user, bot_id, {"status": BotStatus.STOPPED.value, "stopped_at": _now()}
    )
    try:
        command_id = await bus.publish_command(CommandType.STOP, bot_id=bot_id, user_id=user.id)
    except CommandPublishError as exc:
        raise redis_unavailable(exc, "El bot quedó detenido en la base") from exc
    return BotCommandResult(bot=Bot.model_validate(updated), command="stop", command_id=command_id)


async def _kill(
    db: Database, bus: CommandBus, user: CurrentUser, bot_id: UUID, reason: str
) -> BotCommandResult:
    updated = await _update_bot(
        db, user, bot_id, {"status": BotStatus.KILLED.value, "stopped_at": _now()}
    )
    try:
        command_id = await bus.publish_command(
            CommandType.KILL,
            bot_id=bot_id,
            user_id=user.id,
            reason=reason,
            payload={"close_positions": True},
        )
    except CommandPublishError as exc:
        raise redis_unavailable(exc, "El bot quedó en killed en la base") from exc
    return BotCommandResult(bot=Bot.model_validate(updated), command="kill", command_id=command_id)


@router.post(
    "/{bot_id}/kill",
    response_model=BotCommandResult,
    summary="Paro de emergencia de un bot",
    description=(
        "Kill switch manual: marca el bot como `killed` y publica `kill` en `tm:commands` "
        "con `close_positions=true`. El engine vende las posiciones a USDT y lo detiene. "
        "Es idempotente: repetirlo vuelve a publicar el comando."
    ),
    responses=_COMMAND_ERRORS,
)
async def kill_bot(
    bot_id: UUID,
    user: User,
    db: DB,
    bus: Bus,
    body: Annotated[KillRequest | None, Body()] = None,
) -> BotCommandResult:
    return await _kill(db, bus, user, bot_id, (body or KillRequest()).reason)
