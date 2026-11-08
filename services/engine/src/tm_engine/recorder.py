"""Registro de fills: en memoria (pruebas y backtest) o en la tabla `trades` de Supabase.

`SupabaseTradeRecorder` escribe con la service role (las políticas RLS solo dejan leer a los
usuarios; el backend escribe). Cada operación abre una fila al llenarse la entrada y la cierra
(`exit_price`, `closed_at`, `pnl`, `fees`) cuando el sleeve queda sin posición en el símbolo.
La tabla no tiene columna de sleeve: se guarda en `rationale.sleeve` junto con la justificación
completa de la decisión.

Con varios bots en un mismo motor (#19), cada fill trae su `bot_id` (de la etiqueta `bot:<id>`
de la orden) y la fila se escribe para ese bot; `bot_id` del constructor es el respaldo para
los fills sin bot. Las compras que agregan a una posición y las ventas parciales se acumulan
en la misma fila hasta que el sleeve queda sin posición.

Estado tras un reinicio (#89): mientras la fila está abierta, cada fill reescribe
`rationale.position` con lo que el motor sabe de ella:

    "position": {"open_quantity": "0.015", "realized_pnl": "-0.42",
                 "exit_notional": "450.1", "target_fraction": 0.75}

- `open_quantity`: lo que sigue abierto después de las ventas parciales (`quantity` es lo
  comprado en total y `entry_price` su costo promedio).
- `realized_pnl`: P&L realizado por las ventas parciales, ya sin las comisiones pagadas.
- `exit_notional`: USDT recibidos por las ventas parciales (para el `exit_price` promedio).
- `target_fraction`: fracción de la posición objetivo que tiene el sleeve (la escribe el
  BotRunner en `rationale.weights`; en las ventas parciales se escala con la cantidad).

Con eso la tabla `trades` es la fuente de verdad de las posiciones por bot y sleeve: el motor
las restaura al arrancar (`open_positions`) y el BotRunner reconstruye su libro virtual
(`tm_agents.orchestrator.state`).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

import httpx
from tm_shared.contracts import DEFAULT_SLEEVE, OrderSide, TradeDecision

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FillRecord:
    """Un fill ya atribuido a su sleeve."""

    sleeve: str
    symbol: str
    side: OrderSide
    role: str  # entry, sl, tp, exit
    quantity: Decimal
    price: Decimal
    commission: Decimal
    realized_pnl: Decimal
    position_after: Decimal
    client_order_id: str
    ts: datetime
    decision: TradeDecision | None = None
    bot_id: str | None = None


class FillRecorder(Protocol):
    def on_fill(self, record: FillRecord) -> None: ...


@dataclass(frozen=True)
class RestoredPosition:
    """Posición abierta de un bot y sleeve leída de `trades` al arrancar."""

    bot_id: str | None
    sleeve: str
    symbol: str
    quantity: Decimal
    avg_price: Decimal


POSITION_KEY = "position"
TARGET_FRACTION = "target_fraction"


def _target_fraction(decision: TradeDecision | None) -> float | None:
    if decision is None:
        return None
    value = decision.rationale.weights.get(TARGET_FRACTION)
    return float(value) if value is not None else None


@dataclass
class MemoryFillRecorder:
    """Guarda los fills en una lista (backtests y pruebas)."""

    fills: list[FillRecord] = field(default_factory=list)

    def on_fill(self, record: FillRecord) -> None:
        self.fills.append(record)


@dataclass
class _OpenRow:
    id: str
    fees: Decimal
    pnl: Decimal
    entry_notional: Decimal = Decimal("0")
    entry_quantity: Decimal = Decimal("0")
    exit_notional: Decimal = Decimal("0")
    exit_quantity: Decimal = Decimal("0")
    rationale: dict[str, Any] = field(default_factory=dict)
    target_fraction: float | None = None

    @property
    def open_quantity(self) -> Decimal:
        return self.entry_quantity - self.exit_quantity

    def position(self) -> dict[str, Any]:
        return {
            "open_quantity": str(self.open_quantity),
            "realized_pnl": str(self.pnl),
            "exit_notional": str(self.exit_notional),
            TARGET_FRACTION: self.target_fraction,
        }

    def rationale_with_position(self) -> dict[str, Any]:
        self.rationale[POSITION_KEY] = self.position()
        return self.rationale


class SupabaseTradeRecorder:
    """Escribe las operaciones en `public.trades` vía PostgREST.

    Lee `SUPABASE_URL` y `SUPABASE_SERVICE_ROLE_KEY` del entorno si no se pasan.
    """

    def __init__(
        self,
        bot_id: str | None = None,
        *,
        url: str | None = None,
        service_key: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        base = (url or os.environ.get("SUPABASE_URL", "")).rstrip("/")
        key = service_key or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
        if not base or not key:
            raise ValueError("faltan SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY")
        self.bot_id = bot_id
        self._client = client or httpx.Client(timeout=10.0)
        self._endpoint = f"{base}/rest/v1/trades"
        self._headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }
        self._open: dict[tuple[str, str, str], _OpenRow] = {}

    def on_fill(self, record: FillRecord) -> None:
        bot_id = record.bot_id or self.bot_id
        if not bot_id:
            log.warning("fill sin bot: no se registra en Supabase (%s)", record.client_order_id)
            return
        key = (bot_id, record.sleeve, record.symbol)
        if record.side is OrderSide.BUY and key in self._open:
            self._add_to_trade(key, record)
        elif record.side is OrderSide.BUY:
            self._open_trade(key, record)
        elif key in self._open:
            self._close_trade(key, record)
        else:
            log.warning("venta sin operación abierta en Supabase: %s", key)

    def _open_trade(self, key: tuple[str, str, str], record: FillRecord) -> None:
        decision = record.decision
        rationale: dict[str, Any] = (
            decision.rationale.model_dump(mode="json") if decision is not None else {}
        )
        rationale["sleeve"] = record.sleeve
        open_row = _OpenRow(
            id="",
            fees=record.commission,
            pnl=record.realized_pnl,
            entry_notional=record.price * record.quantity,
            entry_quantity=record.quantity,
            rationale=rationale,
            target_fraction=_target_fraction(decision),
        )
        row = {
            "bot_id": key[0],
            "symbol": record.symbol,
            "side": record.side.value,
            "quantity": str(record.quantity),
            "entry_price": str(record.price),
            "stop_loss": str(decision.stop_loss) if decision else None,
            "take_profit": str(decision.take_profit) if decision else None,
            "fees": str(record.commission),
            "rationale": open_row.rationale_with_position(),
            "opened_at": record.ts.isoformat(),
        }
        response = self._client.post(self._endpoint, headers=self._headers, json=row)
        response.raise_for_status()
        open_row.id = response.json()[0]["id"]
        self._open[key] = open_row

    def _add_to_trade(self, key: tuple[str, str, str], record: FillRecord) -> None:
        """Fill parcial adicional de la entrada: actualiza cantidad y precio promedio."""
        row = self._open[key]
        row.fees += record.commission
        row.pnl += record.realized_pnl
        row.entry_notional += record.price * record.quantity
        row.entry_quantity += record.quantity
        fraction = _target_fraction(record.decision)
        if fraction is not None:
            row.target_fraction = fraction
        self._patch(
            row.id,
            {
                "quantity": str(row.entry_quantity),
                "entry_price": str(row.entry_notional / row.entry_quantity),
                "fees": str(row.fees),
                "rationale": row.rationale_with_position(),
            },
        )

    def _close_trade(self, key: tuple[str, str, str], record: FillRecord) -> None:
        row = self._open[key]
        before = row.open_quantity
        row.fees += record.commission
        row.pnl += record.realized_pnl
        row.exit_notional += record.price * record.quantity
        row.exit_quantity += record.quantity
        if record.position_after > 0:
            # Cierre parcial: la fila sigue abierta, pero su posición queda al día en la base.
            if row.target_fraction is not None and before > 0:
                row.target_fraction *= float(row.open_quantity / before)
            self._patch(row.id, {"fees": str(row.fees), "rationale": row.rationale_with_position()})
            return
        exit_price = row.exit_notional / row.exit_quantity
        patch = {
            "exit_price": str(exit_price),
            "closed_at": record.ts.isoformat(),
            "pnl": str(row.pnl),
            "fees": str(row.fees),
        }
        self._patch(row.id, patch)
        del self._open[key]

    def open_positions(self) -> list[RestoredPosition]:
        """Lee las operaciones abiertas de `trades` y las deja listas para seguir registrando.

        Se llama una vez al arrancar el motor (antes del primer fill). Devuelve la posición de
        cada bot y sleeve para `AgenticStrategy.restore_positions`.
        """
        params = {
            "select": "id,bot_id,symbol,quantity,entry_price,fees,rationale",
            "closed_at": "is.null",
            "side": "eq.buy",
            "order": "opened_at.asc",
        }
        response = self._client.get(self._endpoint, headers=self._headers, params=params)
        response.raise_for_status()
        out: list[RestoredPosition] = []
        for raw in response.json():
            try:
                key, row, pos = _open_row_from_db(raw)
            except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
                log.warning("operación abierta ilegible en trades (%s): %s", raw.get("id"), exc)
                continue
            if key in self._open:
                log.warning("dos operaciones abiertas para %s; se usa la primera", key)
                continue
            self._open[key] = row
            out.append(pos)
        return out

    def _patch(self, trade_id: str, patch: dict[str, Any]) -> None:
        response = self._client.patch(
            self._endpoint, params={"id": f"eq.{trade_id}"}, headers=self._headers, json=patch
        )
        response.raise_for_status()


def _open_row_from_db(
    raw: dict[str, Any],
) -> tuple[tuple[str, str, str], _OpenRow, RestoredPosition]:
    """Fila abierta de `trades` -> (llave, estado del recorder, posición restaurada)."""
    rationale = dict(raw.get("rationale") or {})
    sleeve = str(rationale.get("sleeve") or DEFAULT_SLEEVE)
    bought = Decimal(str(raw["quantity"]))
    avg = Decimal(str(raw["entry_price"]))
    position = rationale.get(POSITION_KEY) or {}
    open_qty = Decimal(str(position.get("open_quantity", bought)))
    open_qty = min(max(open_qty, Decimal("0")), bought)
    pnl = Decimal(str(position.get("realized_pnl", -Decimal(str(raw.get("fees") or 0)))))
    fraction = position.get(TARGET_FRACTION)
    sold = bought - open_qty
    row = _OpenRow(
        id=str(raw["id"]),
        fees=Decimal(str(raw.get("fees") or 0)),
        pnl=pnl,
        entry_notional=bought * avg,
        entry_quantity=bought,
        exit_notional=Decimal(str(position.get("exit_notional", sold * avg))),
        exit_quantity=sold,
        rationale=rationale,
        target_fraction=float(fraction) if fraction is not None else None,
    )
    bot_id = str(raw["bot_id"])
    key = (bot_id, sleeve, str(raw["symbol"]))
    restored = RestoredPosition(bot_id, sleeve, key[2], open_qty, avg)
    return key, row, restored
