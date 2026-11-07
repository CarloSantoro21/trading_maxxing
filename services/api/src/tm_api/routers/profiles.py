"""CRUD de perfiles de inversión (uno por usuario)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Response, status

from tm_api.auth import User
from tm_api.deps import DB, get_one, not_found, resolve_profile
from tm_api.schemas import Profile, ProfileCreate, ProfileUpdate

router = APIRouter(prefix="/profiles", tags=["profiles"])

_ERRORS = {401: {"description": "Token ausente o inválido"}}


@router.get("", response_model=list[Profile], summary="Listar mis perfiles", responses=_ERRORS)
async def list_profiles(user: User, db: DB) -> list[dict]:
    return await db.select("profiles", user.token, {"order": "created_at.asc"})


@router.get(
    "/me",
    response_model=Profile,
    summary="Mi perfil",
    responses={**_ERRORS, 404: {"description": "El usuario aún no tiene perfil"}},
)
async def get_my_profile(user: User, db: DB) -> dict:
    return await resolve_profile(db, user, None)


@router.post(
    "",
    response_model=Profile,
    status_code=status.HTTP_201_CREATED,
    summary="Crear mi perfil",
    responses={
        **_ERRORS,
        409: {"description": "El usuario ya tiene un perfil"},
        422: {"description": "Datos inválidos (riesgo 1 a 5, capital > 0, drawdown en (0, 1])"},
    },
)
async def create_profile(body: ProfileCreate, user: User, db: DB) -> dict:
    row = body.model_dump(mode="json")
    row["user_id"] = str(user.id)
    return await db.insert("profiles", user.token, row)


@router.get("/{profile_id}", response_model=Profile, summary="Ver un perfil", responses=_ERRORS)
async def get_profile(profile_id: UUID, user: User, db: DB) -> dict:
    return await get_one(db, "profiles", user, profile_id, "Perfil")


@router.patch(
    "/{profile_id}", response_model=Profile, summary="Actualizar un perfil", responses=_ERRORS
)
async def update_profile(profile_id: UUID, body: ProfileUpdate, user: User, db: DB) -> dict:
    values = body.model_dump(mode="json", exclude_unset=True)
    rows = await db.update("profiles", user.token, {"id": f"eq.{profile_id}"}, values)
    if not rows:
        raise not_found("Perfil")
    return rows[0]


@router.delete(
    "/{profile_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Borrar un perfil (y en cascada sus metas, bots y backtests)",
    responses=_ERRORS,
)
async def delete_profile(profile_id: UUID, user: User, db: DB) -> Response:
    rows = await db.delete("profiles", user.token, {"id": f"eq.{profile_id}"})
    if not rows:
        raise not_found("Perfil")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
