"""Dependencias compartidas por los routers."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status

from tm_api.auth import CurrentUser
from tm_api.commands import CommandBus, CommandPublishError
from tm_api.db import Database, Row


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_bus(request: Request) -> CommandBus:
    return request.app.state.bus


DB = Annotated[Database, Depends(get_db)]
Bus = Annotated[CommandBus, Depends(get_bus)]


def not_found(resource: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{resource} no encontrado")


def redis_unavailable(exc: CommandPublishError, what: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"{what}, pero no se pudo publicar en Redis: {exc}",
    )


async def get_one(db: Database, table: str, user: CurrentUser, row_id: UUID, resource: str) -> Row:
    """Lee una fila por id. Con RLS, una fila ajena simplemente no aparece: 404."""
    rows = await db.select(table, user.token, {"id": f"eq.{row_id}", "limit": "1"})
    if not rows:
        raise not_found(resource)
    return rows[0]


async def resolve_profile(db: Database, user: CurrentUser, profile_id: UUID | None) -> Row:
    """Devuelve el perfil indicado o, si no se indica, el único perfil del usuario."""
    params = {"limit": "1"}
    params["id" if profile_id else "user_id"] = f"eq.{profile_id or user.id}"
    rows = await db.select("profiles", user.token, params)
    if not rows:
        if profile_id:
            raise not_found("Perfil")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aún no tienes perfil de inversión; créalo con POST /profiles",
        )
    return rows[0]
