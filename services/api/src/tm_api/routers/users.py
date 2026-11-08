"""Usuario actual (`GET /me`) y alta de usuarios del equipo (`POST /admin/users`, #67)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from tm_api.auth import User
from tm_api.deps import DB, Admin, UserAdminDep, get_role
from tm_api.schemas import CreatedUser, Me, UserCreate
from tm_api.users import UserAdminError, UserExistsError

router = APIRouter(tags=["users"])

_ERRORS = {401: {"description": "Token ausente o inválido"}}


@router.get("/me", response_model=Me, summary="Quién soy y qué rol tengo", responses=_ERRORS)
async def get_me(user: User, db: DB) -> dict:
    return {"id": user.id, "email": user.email, "role": await get_role(db, user)}


@router.post(
    "/admin/users",
    response_model=CreatedUser,
    status_code=status.HTTP_201_CREATED,
    summary="Dar de alta un usuario con contraseña temporal (solo admin)",
    responses={
        **_ERRORS,
        403: {"description": "El usuario no es admin"},
        409: {"description": "Ya existe un usuario con ese correo"},
        502: {"description": "Supabase Auth rechazó el alta"},
        503: {"description": "Falta SUPABASE_SERVICE_ROLE_KEY en la API"},
    },
)
async def create_user(body: UserCreate, admin: Admin, user_admin: UserAdminDep) -> dict:
    try:
        new = await user_admin.create_user(body.email, body.role)
    except UserExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except UserAdminError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return {
        "id": new.id,
        "email": new.email,
        "role": new.role,
        "temporary_password": new.temporary_password,
    }
