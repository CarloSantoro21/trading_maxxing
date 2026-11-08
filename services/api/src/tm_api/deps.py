"""Dependencias compartidas por los routers."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status

from tm_api.auth import CurrentUser, User
from tm_api.commands import CommandBus, CommandPublishError
from tm_api.db import Database, Row
from tm_api.schemas import AppRole
from tm_api.users import UserAdmin


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_bus(request: Request) -> CommandBus:
    return request.app.state.bus


def get_user_admin(request: Request) -> UserAdmin:
    user_admin: UserAdmin | None = request.app.state.user_admin
    if user_admin is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El alta de usuarios no está configurada: falta SUPABASE_SERVICE_ROLE_KEY",
        )
    return user_admin


DB = Annotated[Database, Depends(get_db)]
Bus = Annotated[CommandBus, Depends(get_bus)]
UserAdminDep = Annotated[UserAdmin, Depends(get_user_admin)]


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


# --- roles ---------------------------------------------------------------------


async def get_role(db: Database, user: CurrentUser) -> AppRole:
    """Rol del usuario según `app_roles` (RLS: cada quien lee solo el suyo).

    Sin fila, el usuario es member: nadie es admin por accidente.
    """
    rows = await db.select(
        "app_roles", user.token, {"user_id": f"eq.{user.id}", "select": "role", "limit": "1"}
    )
    if rows and rows[0].get("role") == AppRole.ADMIN.value:
        return AppRole.ADMIN
    return AppRole.MEMBER


async def require_admin(user: User, db: DB) -> CurrentUser:
    """Dependencia de las acciones de admin: 403 si el usuario es member.

    Es una primera barrera con un mensaje claro; la base vuelve a exigir el rol (la política
    de strategy_transitions usa private.is_admin()).
    """
    if await get_role(db, user) is not AppRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo un admin puede promover o retirar estrategias y dar de alta usuarios",
        )
    return user


Admin = Annotated[CurrentUser, Depends(require_admin)]
