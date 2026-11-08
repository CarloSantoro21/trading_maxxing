"""CRUD de metas de rendimiento de un perfil y su probabilidad de alcanzarlas (#38)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from starlette.concurrency import run_in_threadpool

from tm_api.auth import User
from tm_api.db import UNPROCESSABLE
from tm_api.deps import DB, get_one, not_found, resolve_profile
from tm_api.goal_projection import load_projection_inputs
from tm_api.projection import ProjectionError, project_goal
from tm_api.schemas import Goal, GoalCreate, GoalProbability, GoalUpdate

router = APIRouter(prefix="/goals", tags=["goals"])

_ERRORS = {401: {"description": "Token ausente o inválido"}}


@router.get("", response_model=list[Goal], summary="Listar mis metas", responses=_ERRORS)
async def list_goals(
    user: User,
    db: DB,
    profile_id: Annotated[UUID | None, Query(description="Filtrar por perfil")] = None,
) -> list[dict]:
    params = {"order": "deadline.asc"}
    if profile_id:
        params["profile_id"] = f"eq.{profile_id}"
    return await db.select("goals", user.token, params)


@router.post(
    "",
    response_model=Goal,
    status_code=status.HTTP_201_CREATED,
    summary="Crear una meta",
    responses={
        **_ERRORS,
        404: {"description": "El perfil no existe o no es tuyo"},
        422: {"description": "Retorno objetivo ≤ 0 o fecha en el pasado"},
    },
)
async def create_goal(body: GoalCreate, user: User, db: DB) -> dict:
    # Verificar el perfil primero da un 404 claro en vez del 403 de RLS.
    await resolve_profile(db, user, body.profile_id)
    return await db.insert("goals", user.token, body.model_dump(mode="json"))


@router.get("/{goal_id}", response_model=Goal, summary="Ver una meta", responses=_ERRORS)
async def get_goal(goal_id: UUID, user: User, db: DB) -> dict:
    return await get_one(db, "goals", user, goal_id, "Meta")


@router.patch("/{goal_id}", response_model=Goal, summary="Actualizar una meta", responses=_ERRORS)
async def update_goal(goal_id: UUID, body: GoalUpdate, user: User, db: DB) -> dict:
    values = body.model_dump(mode="json", exclude_unset=True)
    rows = await db.update("goals", user.token, {"id": f"eq.{goal_id}"}, values)
    if not rows:
        raise not_found("Meta")
    return rows[0]


@router.delete(
    "/{goal_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Borrar una meta",
    responses=_ERRORS,
)
async def delete_goal(goal_id: UUID, user: User, db: DB) -> Response:
    rows = await db.delete("goals", user.token, {"id": f"eq.{goal_id}"})
    if not rows:
        raise not_found("Meta")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{goal_id}/probability",
    response_model=GoalProbability,
    summary="Probabilidad de alcanzar la meta",
    description=(
        "Monte Carlo #3: proyecta tu equity con las estadísticas del portafolio de "
        "referencia de las estrategias que reciben capital (o, si aún no tienen historia, "
        "con su out-of-sample del laboratorio), con el peso de cada sleeve y la exposición "
        "de tu nivel de riesgo, por block bootstrap. Devuelve la probabilidad de tocar la "
        "meta antes de la fecha límite y el abanico p10/p50/p90."
    ),
    responses={
        **_ERRORS,
        404: {"description": "La meta no existe o no es tuya"},
        422: {"description": "La fecha límite ya pasó o está a más de 10 años"},
    },
)
async def get_goal_probability(goal_id: UUID, user: User, db: DB, request: Request) -> dict:
    goal = await get_one(db, "goals", user, goal_id, "Meta")
    inputs = await load_projection_inputs(
        db, user, goal, request.app.state.settings.lab_reports_dir
    )
    try:
        result = await run_in_threadpool(
            lambda: project_goal(
                start_equity=inputs.start_equity,
                target_equity=inputs.target_equity,
                risk_level=inputs.risk_level,
                horizon=inputs.horizon,
                today=inputs.today,
                deadline=inputs.deadline,
                strategies=inputs.strategies,
            )
        )
    except ProjectionError as exc:
        raise HTTPException(status_code=UNPROCESSABLE, detail=str(exc)) from exc
    return {
        **asdict(result),
        "goal_id": goal["id"],
        "profile_id": inputs.profile_id,
        "target_return": float(goal["target_return"]),
        "deadline": inputs.deadline,
        "as_of": inputs.today,
        "capital": inputs.capital,
    }
