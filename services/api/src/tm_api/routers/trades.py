"""Historial de operaciones (solo lectura; las escribe el engine con la service role)."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query

from tm_api.auth import User
from tm_api.deps import DB
from tm_api.schemas import Symbol, TradePage

router = APIRouter(prefix="/trades", tags=["trades"])

MAX_PAGE = 200


@router.get(
    "",
    response_model=TradePage,
    summary="Listar mis operaciones (paginado)",
    description=(
        "Operaciones de todos los bots del usuario, de la más reciente a la más antigua. "
        "`total` es el número de filas que cumplen el filtro (de `Content-Range`)."
    ),
    responses={401: {"description": "Token ausente o inválido"}},
)
async def list_trades(
    user: User,
    db: DB,
    bot_id: Annotated[UUID | None, Query(description="Filtrar por bot")] = None,
    symbol: Annotated[Symbol | None, Query(description="Filtrar por par")] = None,
    state: Annotated[
        Literal["open", "closed"] | None,
        Query(description="`open` = sin cerrar, `closed` = cerradas"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE, description="Tamaño de página")] = 50,
    offset: Annotated[int, Query(ge=0, description="Filas a saltar")] = 0,
) -> TradePage:
    params = {"order": "opened_at.desc,id.desc"}
    if bot_id:
        params["bot_id"] = f"eq.{bot_id}"
    if symbol:
        params["symbol"] = f"eq.{symbol}"
    if state == "open":
        params["closed_at"] = "is.null"
    elif state == "closed":
        params["closed_at"] = "not.is.null"
    rows, total = await db.select_page("trades", user.token, params, limit=limit, offset=offset)
    return TradePage.model_validate(
        {"items": rows, "total": total, "limit": limit, "offset": offset}
    )
