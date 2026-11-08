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
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

import httpx
from tm_shared.contracts import OrderSide, TradeDecision

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
        row = {
            "bot_id": key[0],
            "symbol": record.symbol,
            "side": record.side.value,
            "quantity": str(record.quantity),
            "entry_price": str(record.price),
            "stop_loss": str(decision.stop_loss) if decision else None,
            "take_profit": str(decision.take_profit) if decision else None,
            "fees": str(record.commission),
            "rationale": rationale,
            "opened_at": record.ts.isoformat(),
        }
        response = self._client.post(self._endpoint, headers=self._headers, json=row)
        response.raise_for_status()
        trade_id = response.json()[0]["id"]
        self._open[key] = _OpenRow(
            id=trade_id,
            fees=record.commission,
            pnl=record.realized_pnl,
            entry_notional=record.price * record.quantity,
            entry_quantity=record.quantity,
        )

    def _add_to_trade(self, key: tuple[str, str, str], record: FillRecord) -> None:
        """Fill parcial adicional de la entrada: actualiza cantidad y precio promedio."""
        row = self._open[key]
        row.fees += record.commission
        row.pnl += record.realized_pnl
        row.entry_notional += record.price * record.quantity
        row.entry_quantity += record.quantity
        self._patch(
            row.id,
            {
                "quantity": str(row.entry_quantity),
                "entry_price": str(row.entry_notional / row.entry_quantity),
                "fees": str(row.fees),
            },
        )

    def _close_trade(self, key: tuple[str, str, str], record: FillRecord) -> None:
        row = self._open[key]
        row.fees += record.commission
        row.pnl += record.realized_pnl
        row.exit_notional += record.price * record.quantity
        row.exit_quantity += record.quantity
        if record.position_after > 0:
            return  # cierre parcial: se espera al resto
        exit_price = row.exit_notional / row.exit_quantity
        patch = {
            "exit_price": str(exit_price),
            "closed_at": record.ts.isoformat(),
            "pnl": str(row.pnl),
            "fees": str(row.fees),
        }
        self._patch(row.id, patch)
        del self._open[key]

    def _patch(self, trade_id: str, patch: dict[str, Any]) -> None:
        response = self._client.patch(
            self._endpoint, params={"id": f"eq.{trade_id}"}, headers=self._headers, json=patch
        )
        response.raise_for_status()
