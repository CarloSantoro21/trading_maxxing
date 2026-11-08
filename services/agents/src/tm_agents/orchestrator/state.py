"""Estado de cada bot tras un reinicio: el libro virtual se reconstruye desde ``trades`` (#89).

**Fuente de verdad: la tabla ``trades``.** La escribe el motor (``SupabaseTradeRecorder`` de
``tm_engine``) con los fills reales, una fila por operación de cada bot y sleeve:

- abierta (``closed_at`` nulo): ``quantity`` es lo comprado, ``entry_price`` su costo promedio y
  ``rationale.position`` lo que sigue abierto (``open_quantity``), el P&L de las ventas
  parciales (``realized_pnl``) y la fracción de la posición objetivo (``target_fraction``);
- cerrada: ``pnl`` es el resultado final ya sin comisiones.

El libro virtual del BotRunner es optimista (supone que cada decisión se llena al precio del
evento), pero al arrancar se alinea con lo que el motor sí ejecutó:

- posiciones: las filas abiertas, por ``rationale.sleeve`` y símbolo;
- efectivo: ``capital + P&L cerrado + P&L parcial de las abiertas - costo de lo abierto``;
- pico del equity: el máximo de la curva de P&L cerrado (en orden de cierre) o el equity
  actual, lo que sea mayor (cota inferior del pico real; los máximos a mercado de posiciones
  que ya se cerraron no se guardan);
- SL/TP reales solo para las estrategias con stop real (``signal_levels`` en su política).

No se reconstruye el libro shadow (sus decisiones nunca llegan al motor; el ``ReferenceService``
lleva el de cada estrategia) ni el contador de operaciones del día.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import httpx
from tm_shared.contracts import DEFAULT_SLEEVE

log = logging.getLogger(__name__)

ZERO = Decimal("0")
TRADE_SELECT = (
    "id,symbol,side,quantity,entry_price,stop_loss,take_profit,fees,pnl,rationale,"
    "opened_at,closed_at"
)
PAGE = 1000


@dataclass(frozen=True)
class OpenPosition:
    """Una operación abierta de un sleeve, tal como la dejó el motor."""

    sleeve: str
    symbol: str
    quantity: Decimal
    avg_price: Decimal
    realized_pnl: Decimal = ZERO
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    target_fraction: float | None = None


@dataclass(frozen=True)
class BotState:
    """Lo que el bot tenía antes de reiniciarse (ver el módulo)."""

    bot_id: str
    positions: tuple[OpenPosition, ...] = ()
    closed_pnl: Decimal = ZERO
    #: Máximo de la curva de P&L cerrado (≥ 0), para el pico del equity.
    peak_closed_pnl: Decimal = ZERO


class TradeStateRepository(Protocol):
    def load(self, bot_id: str) -> BotState: ...


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return out if out.is_finite() else None


def open_position_from_row(row: Mapping[str, Any]) -> OpenPosition | None:
    """Fila abierta de ``trades`` -> ``OpenPosition`` (``None`` si ya no queda nada abierto)."""
    rationale = row.get("rationale") or {}
    position = rationale.get("position") or {}
    bought = _dec(row["quantity"])
    avg = _dec(row["entry_price"])
    if bought is None or avg is None:
        raise ValueError("quantity o entry_price inválidos")
    quantity = _dec(position.get("open_quantity"))
    quantity = bought if quantity is None else min(max(quantity, ZERO), bought)
    if quantity <= 0:
        return None
    fees = _dec(row.get("fees")) or ZERO
    realized = _dec(position.get("realized_pnl"))
    fraction = position.get("target_fraction")
    if fraction is None:
        fraction = (rationale.get("weights") or {}).get("target_fraction")
    return OpenPosition(
        sleeve=str(rationale.get("sleeve") or DEFAULT_SLEEVE),
        symbol=str(row["symbol"]),
        quantity=quantity,
        avg_price=avg,
        realized_pnl=-fees if realized is None else realized,
        stop_loss=_dec(row.get("stop_loss")),
        take_profit=_dec(row.get("take_profit")),
        target_fraction=float(fraction) if fraction is not None else None,
    )


def state_from_rows(bot_id: str, rows: Iterable[Mapping[str, Any]]) -> BotState:
    """Estado del bot a partir de sus filas de ``trades`` (abiertas y cerradas)."""
    positions: list[OpenPosition] = []
    seen: set[tuple[str, str]] = set()
    closed: list[tuple[str, Decimal]] = []
    for row in rows:
        if row.get("side", "buy") != "buy":
            continue
        try:
            if row.get("closed_at"):
                closed.append((str(row["closed_at"]), _dec(row.get("pnl")) or ZERO))
                continue
            pos = open_position_from_row(row)
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("operación ilegible del bot %s (%s): %s", bot_id, row.get("id"), exc)
            continue
        if pos is None:
            continue
        key = (pos.sleeve, pos.symbol)
        if key in seen:
            # El motor lleva una sola operación abierta por bot, sleeve y símbolo.
            log.warning("bot %s: dos operaciones abiertas en %s; se usa la primera", bot_id, key)
            continue
        seen.add(key)
        positions.append(pos)
    total = peak = ZERO
    for _closed_at, pnl in sorted(closed, key=lambda c: c[0]):
        total += pnl
        peak = max(peak, total)
    return BotState(bot_id, tuple(positions), total, peak)


class InMemoryTradeStateRepository:
    """Filas de ``trades`` en memoria por bot (backtest y pruebas)."""

    def __init__(self, rows: Mapping[str, Sequence[Mapping[str, Any]]] | None = None) -> None:
        self.rows = {bot: list(bot_rows) for bot, bot_rows in (rows or {}).items()}

    def load(self, bot_id: str) -> BotState:
        return state_from_rows(bot_id, self.rows.get(bot_id, ()))


class PostgRESTTradeStateRepository:
    """Lee ``trades`` de Supabase con la llave ``service_role`` (salta RLS)."""

    def __init__(
        self, url: str, key: str, *, client: httpx.Client | None = None, page: int = PAGE
    ) -> None:
        if not url or not key:
            raise ValueError("faltan SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY")
        self._base = url.rstrip("/") + "/rest/v1/trades"
        self._headers = {"apikey": key}
        if key.startswith("eyJ"):
            self._headers["Authorization"] = f"Bearer {key}"
        self._client = client or httpx.Client(timeout=10.0)
        self._page = page

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, **kw: Any
    ) -> PostgRESTTradeStateRepository:
        env = os.environ if env is None else env
        return cls(env.get("SUPABASE_URL", ""), env.get("SUPABASE_SERVICE_ROLE_KEY", ""), **kw)

    def rows(self, bot_id: str) -> list[Mapping[str, Any]]:
        out: list[Mapping[str, Any]] = []
        offset = 0
        while True:
            params = {
                "select": TRADE_SELECT,
                "bot_id": f"eq.{bot_id}",
                "order": "opened_at.asc,id.asc",
                "limit": str(self._page),
                "offset": str(offset),
            }
            resp = self._client.get(self._base, params=params, headers=self._headers)
            resp.raise_for_status()
            batch = resp.json()
            out.extend(batch)
            if len(batch) < self._page:
                return out
            offset += self._page

    def load(self, bot_id: str) -> BotState:
        return state_from_rows(bot_id, self.rows(bot_id))


__all__ = [
    "BotState",
    "InMemoryTradeStateRepository",
    "OpenPosition",
    "PostgRESTTradeStateRepository",
    "TradeStateRepository",
    "open_position_from_row",
    "state_from_rows",
]
