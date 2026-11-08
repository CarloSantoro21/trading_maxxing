"""Catálogo de estrategias y acciones de admin sobre su ciclo de vida (#55, #58, #67).

Cualquier usuario autenticado lee las estrategias (son compartidas). Promover
(`shadow`/`probation` -> `active`) y retirar (cualquier estado -> `retired`) son acciones de
admin: la API responde 403 a un member y la base lo vuelve a exigir con RLS. La transición se
registra con el user_id del admin como actor, en `strategy_transitions`, cuyo trigger valida
las reglas del ciclo de vida y actualiza `strategies.state`.

Promover desde shadow exige al menos `MIN_SHADOW_DAYS` días en shadow sin fallas (propuesta,
sección 3.3). Los días se cuentan igual que la columna calculada `shadow_days_clean` de la
base: desde lo más reciente entre `state_changed_at` y `live_metrics.shadow.clean_since`.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status

from tm_api.auth import CurrentUser, User
from tm_api.db import Database, Row
from tm_api.deps import DB, Admin, not_found
from tm_api.schemas import (
    Strategy,
    StrategyState,
    StrategyTransition,
    StrategyTransitionRequest,
)

router = APIRouter(prefix="/strategies", tags=["strategies"])

_ERRORS = {401: {"description": "Token ausente o inválido"}}
_ADMIN_ERRORS = {
    **_ERRORS,
    403: {"description": "El usuario no es admin"},
    404: {"description": "No existe la estrategia"},
    409: {
        "description": (
            "La estrategia no está en un estado desde el que se permita, o lleva menos de "
            "7 días en shadow sin fallas"
        )
    },
}

#: Estados desde los que se puede promover a active (solo una persona lo hace).
PROMOTABLE = frozenset({StrategyState.SHADOW, StrategyState.PROBATION})
#: Días mínimos en shadow sin fallas para promover (igual que tm_agents.reference).
MIN_SHADOW_DAYS = 7


def _parse_ts(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        ts = value
    elif isinstance(value, str) and value:
        try:
            ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def shadow_days_clean(strategy: Mapping[str, Any], now: datetime | None = None) -> float | None:
    """Días en shadow sin fallas; ``None`` si la estrategia no está en shadow.

    Replica ``public.shadow_days_clean``: cuenta desde lo más reciente entre la entrada al
    estado y ``live_metrics.shadow.clean_since`` (la última falla, la escribe el
    ReferenceService).
    """
    if strategy.get("state") != StrategyState.SHADOW.value:
        return None
    changed = _parse_ts(strategy.get("state_changed_at"))
    if changed is None:
        return 0.0
    metrics = strategy.get("live_metrics") or {}
    shadow = metrics.get("shadow") if isinstance(metrics, Mapping) else None
    clean = _parse_ts(shadow.get("clean_since")) if isinstance(shadow, Mapping) else None
    since = max(changed, clean) if clean else changed
    now = now or datetime.now(UTC)
    return max(0.0, (now - since).total_seconds() / 86_400)


async def _get(db: Database, user: CurrentUser, name: str) -> Row:
    rows = await db.select("strategies", user.token, {"name": f"eq.{name}", "limit": "1"})
    if not rows:
        raise not_found("Estrategia")
    return rows[0]


async def _transition(
    db: Database, user: CurrentUser, name: str, to_state: StrategyState, reason: str
) -> dict:
    strategy = await _get(db, user, name)
    from_state = StrategyState(strategy["state"])
    if to_state is StrategyState.ACTIVE and from_state not in PROMOTABLE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Solo se promueve desde shadow o probation; {name} está en {from_state}",
        )
    if to_state is StrategyState.ACTIVE and from_state is StrategyState.SHADOW:
        days = shadow_days_clean(strategy) or 0.0
        if days < MIN_SHADOW_DAYS:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"{name} lleva {days:.1f} días en shadow sin fallas; "
                    f"se requieren al menos {MIN_SHADOW_DAYS}"
                ),
            )
    if to_state is StrategyState.RETIRED and from_state is StrategyState.RETIRED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"{name} ya está retirada")
    await db.insert(
        "strategy_transitions",
        user.token,
        {
            "strategy_id": strategy["id"],
            "from_state": from_state.value,
            "to_state": to_state.value,
            "actor": str(user.id),
            "reason": reason,
        },
    )
    return {
        "strategy": await _get(db, user, name),
        "from_state": from_state,
        "to_state": to_state,
        "actor": str(user.id),
        "reason": reason,
    }


@router.get("", response_model=list[Strategy], summary="Listar estrategias", responses=_ERRORS)
async def list_strategies(user: User, db: DB) -> list[dict]:
    return await db.select("strategies", user.token, {"order": "name.asc"})


@router.get(
    "/{name}",
    response_model=Strategy,
    summary="Ver una estrategia",
    responses={**_ERRORS, 404: {"description": "No existe la estrategia"}},
)
async def get_strategy(name: str, user: User, db: DB) -> dict:
    return await _get(db, user, name)


@router.post(
    "/{name}/promote",
    response_model=StrategyTransition,
    summary="Promover a active (solo admin)",
    responses=_ADMIN_ERRORS,
)
async def promote_strategy(
    name: str, body: StrategyTransitionRequest, admin: Admin, db: DB
) -> dict:
    return await _transition(db, admin, name, StrategyState.ACTIVE, body.reason)


@router.post(
    "/{name}/retire",
    response_model=StrategyTransition,
    summary="Retirar a mano (solo admin)",
    responses=_ADMIN_ERRORS,
)
async def retire_strategy(name: str, body: StrategyTransitionRequest, admin: Admin, db: DB) -> dict:
    return await _transition(db, admin, name, StrategyState.RETIRED, body.reason)
