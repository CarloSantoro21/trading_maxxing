"""Portafolio: suma de los sleeves (uno por bot) de un perfil.

Cada bot es un sleeve con su propio capital y su P&L. De cada uno se toma la última foto
de `equity_snapshots`, sus operaciones abiertas y el P&L realizado de las cerradas.
Mientras ningún bot haya escrito equity, el portafolio vale el capital del perfil en USDT
(en spot, sin posición = todo en USDT).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from tm_api.auth import User
from tm_api.db import Row
from tm_api.deps import DB, resolve_profile
from tm_api.schemas import Portfolio, Sleeve, Trade

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


def _in(ids: list[str]) -> str:
    return f"in.({','.join(ids)})"


def _num(value: object) -> float:
    return float(value) if value is not None else 0.0  # type: ignore[arg-type]


@router.get(
    "",
    response_model=Portfolio,
    summary="Mi portafolio",
    description=(
        "Equity, efectivo, posiciones abiertas y P&L realizado por sleeve y en total. "
        "Si se omite `profile_id` se usa el perfil del usuario."
    ),
    responses={
        401: {"description": "Token ausente o inválido"},
        404: {"description": "El usuario no tiene perfil"},
    },
)
async def get_portfolio(
    user: User,
    db: DB,
    profile_id: Annotated[UUID | None, Query(description="Perfil a consultar")] = None,
) -> Portfolio:
    profile = await resolve_profile(db, user, profile_id)
    capital = _num(profile["capital"])
    bots = await db.select(
        "bots", user.token, {"profile_id": f"eq.{profile['id']}", "order": "created_at.asc"}
    )
    ids = [str(bot["id"]) for bot in bots]

    snapshots: dict[str, Row] = {}
    open_trades: list[Row] = []
    realized: dict[str, float] = defaultdict(float)
    if ids:
        # Una consulta por bot para su última foto: son pocos bots por perfil.
        for bot_id in ids:
            rows = await db.select(
                "equity_snapshots",
                user.token,
                {"bot_id": f"eq.{bot_id}", "order": "ts.desc", "limit": "1"},
            )
            if rows:
                snapshots[bot_id] = rows[0]
        open_trades = await db.select(
            "trades",
            user.token,
            {"bot_id": _in(ids), "closed_at": "is.null", "order": "opened_at.desc"},
        )
        closed = await db.select(
            "trades",
            user.token,
            {"bot_id": _in(ids), "closed_at": "not.is.null", "select": "bot_id,pnl"},
        )
        for trade in closed:
            realized[str(trade["bot_id"])] += _num(trade.get("pnl"))

    open_count: dict[str, int] = defaultdict(int)
    for trade in open_trades:
        open_count[str(trade["bot_id"])] += 1

    sleeves: list[Sleeve] = []
    for bot in bots:
        bot_id = str(bot["id"])
        snap = snapshots.get(bot_id)
        sleeves.append(
            Sleeve(
                bot_id=bot["id"],
                name=bot["name"],
                status=bot["status"],
                equity=_num(snap["equity"]) if snap else None,
                cash=_num(snap["cash"]) if snap else None,
                positions_value=_num(snap["positions_value"]) if snap else None,
                drawdown=_num(snap["drawdown"]) if snap else None,
                as_of=snap["ts"] if snap else None,
                realized_pnl=realized[bot_id],
                open_trades=open_count[bot_id],
            )
        )

    if snapshots:
        equity = sum(_num(s["equity"]) for s in snapshots.values())
        cash = sum(_num(s["cash"]) for s in snapshots.values())
        positions_value = sum(_num(s["positions_value"]) for s in snapshots.values())
    else:
        equity, cash, positions_value = capital, capital, 0.0

    return Portfolio(
        profile_id=profile["id"],
        capital=capital,
        max_drawdown=_num(profile["max_drawdown"]),
        equity=equity,
        cash=cash,
        positions_value=positions_value,
        realized_pnl=sum(realized.values()),
        total_return=(equity - capital) / capital if capital else 0.0,
        sleeves=sleeves,
        open_positions=[Trade.model_validate(t) for t in open_trades],
    )
