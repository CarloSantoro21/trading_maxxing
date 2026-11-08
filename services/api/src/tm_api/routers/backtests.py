"""Backtests: el usuario los encola; el worker de backtests los corre y escribe el reporte.

`POST /backtests` inserta la fila en estado `queued` (lo único que RLS permite al usuario)
y publica el job en el stream `tm:backtests`. Si Redis falla, la fila se borra para no
dejar un job huérfano que nadie va a correr.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from tm_api.auth import User
from tm_api.commands import CommandPublishError
from tm_api.db import Row
from tm_api.deps import DB, Bus, get_one, redis_unavailable, resolve_profile
from tm_api.schemas import Backtest, BacktestCreate, BacktestStatus

router = APIRouter(prefix="/backtests", tags=["backtests"])

_ERRORS = {401: {"description": "Token ausente o inválido"}}


@router.get("", response_model=list[Backtest], summary="Listar mis backtests", responses=_ERRORS)
async def list_backtests(
    user: User,
    db: DB,
    bt_status: Annotated[BacktestStatus | None, Query(alias="status", description="Estado")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[Row]:
    params = {"order": "created_at.desc", "limit": str(limit)}
    if bt_status:
        params["status"] = f"eq.{bt_status.value}"
    return await db.select("backtests", user.token, params)


@router.post(
    "",
    response_model=Backtest,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Encolar un backtest",
    description=(
        "Crea el backtest en estado `queued` y publica el job en `tm:backtests`. "
        "Consulta el avance con `GET /backtests/{id}`."
    ),
    responses={
        **_ERRORS,
        404: {"description": "El perfil no existe o no es tuyo"},
        422: {"description": "Configuración inválida (símbolos, fechas o timeframe)"},
        503: {"description": "Redis no está disponible; el backtest no se encoló"},
    },
)
async def create_backtest(body: BacktestCreate, user: User, db: DB, bus: Bus) -> Row:
    profile = await resolve_profile(db, user, body.profile_id)
    config = body.config.model_dump(mode="json", exclude_none=True)
    row = await db.insert(
        "backtests",
        user.token,
        {"profile_id": profile["id"], "status": BacktestStatus.QUEUED.value, "config": config},
    )
    try:
        await bus.publish_backtest(
            backtest_id=UUID(str(row["id"])),
            profile_id=UUID(str(profile["id"])),
            user_id=user.id,
            config=config,
        )
    except CommandPublishError as exc:
        await db.delete("backtests", user.token, {"id": f"eq.{row['id']}"})
        raise redis_unavailable(exc, "El backtest no se encoló") from exc
    return row


@router.get("/{backtest_id}", response_model=Backtest, summary="Ver un backtest", responses=_ERRORS)
async def get_backtest(backtest_id: UUID, user: User, db: DB) -> Row:
    return await get_one(db, "backtests", user, backtest_id, "Backtest")
