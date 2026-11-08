"""Persistencia del ledger de referencia: ``reference_trades`` y ``reference_equity``.

Las tablas están en la migración ``20261109000200_reference_ledger.sql``. Las escribe solo el
backend con la llave ``service_role``; cualquier usuario autenticado las lee (página
``/strategies``, #58). Hay dos backends: :class:`InMemoryReferenceStore` (pruebas) y
:class:`PostgRESTReferenceStore` (Supabase).

Las escrituras son idempotentes: los trades tienen llave única ``(strategy_id, symbol,
closed_at)`` y se insertan ignorando duplicados, y la equity diaria hace *upsert* sobre
``(strategy_id, day)``. Así se pueden reintentar sin duplicar filas.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol
from uuid import UUID

import httpx

from tm_agents.reference.ledger import ReferenceTrade
from tm_agents.strategy_stats import StrategyState


class ReferenceStoreError(Exception):
    """No se pudo guardar en la base."""


@dataclass(frozen=True)
class EquityPoint:
    """Una fila de ``reference_equity``: el equity del ledger al final de un día UTC."""

    strategy_id: UUID
    started_at: datetime
    day: date
    state: StrategyState
    equity: float
    cash: float
    positions_value: float
    drawdown: float

    def to_row(self) -> dict[str, Any]:
        return {
            "strategy_id": str(self.strategy_id),
            "started_at": self.started_at.isoformat(),
            "day": self.day.isoformat(),
            "state": StrategyState(self.state).value,
            "equity": round(self.equity, 8),
            "cash": round(max(0.0, self.cash), 8),
            "positions_value": round(max(0.0, self.positions_value), 8),
            "drawdown": min(1.0, max(0.0, self.drawdown)),
        }


def trade_row(
    strategy_id: UUID, started_at: datetime, state: StrategyState, trade: ReferenceTrade
) -> dict[str, Any]:
    """Fila de ``reference_trades`` para un trade cerrado."""
    return {
        "strategy_id": str(strategy_id),
        "started_at": started_at.isoformat(),
        "state": StrategyState(state).value,
        "symbol": trade.symbol,
        "quantity": trade.quantity,
        "entry_price": trade.entry_price,
        "exit_price": trade.exit_price,
        "fees": trade.fees,
        "pnl": trade.pnl,
        "ret": trade.ret,
        "opened_at": trade.opened_at.isoformat(),
        "closed_at": trade.closed_at.isoformat(),
    }


class ReferenceStore(Protocol):
    """Dónde se guardan los trades y la equity diaria del ledger de referencia."""

    def save_trades(self, rows: Sequence[Mapping[str, Any]]) -> None: ...

    def upsert_equity(self, points: Sequence[EquityPoint]) -> None: ...


class InMemoryReferenceStore:
    """Backend en memoria con las mismas llaves únicas que la base."""

    def __init__(self) -> None:
        self.trades: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.equity: dict[tuple[str, str], dict[str, Any]] = {}

    def save_trades(self, rows: Sequence[Mapping[str, Any]]) -> None:
        for row in rows:
            key = (row["strategy_id"], row["symbol"], row["closed_at"])
            self.trades.setdefault(key, dict(row))

    def upsert_equity(self, points: Sequence[EquityPoint]) -> None:
        for point in points:
            row = point.to_row()
            self.equity[(row["strategy_id"], row["day"])] = row

    def trades_for(self, strategy_id: UUID) -> list[dict[str, Any]]:
        return sorted(
            (r for r in self.trades.values() if r["strategy_id"] == str(strategy_id)),
            key=lambda r: r["closed_at"],
        )

    def equity_for(self, strategy_id: UUID) -> list[dict[str, Any]]:
        return sorted(
            (r for r in self.equity.values() if r["strategy_id"] == str(strategy_id)),
            key=lambda r: r["day"],
        )


class PostgRESTReferenceStore:
    """Backend sobre PostgREST de Supabase con la llave ``service_role``."""

    def __init__(
        self,
        url: str,
        key: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 10.0,
    ) -> None:
        if not url:
            raise ValueError("falta la URL de Supabase")
        if not key:
            raise ValueError("falta la llave service_role de Supabase")
        headers = {"apikey": key, "Content-Type": "application/json"}
        if key.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {key}"
        self._base = url.rstrip("/") + "/rest/v1"
        self._client = client or httpx.Client(timeout=timeout)
        self._headers = headers

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, **kwargs: Any
    ) -> PostgRESTReferenceStore:
        """Lee ``SUPABASE_URL`` y ``SUPABASE_SERVICE_ROLE_KEY`` del entorno."""
        env = os.environ if env is None else env
        return cls(env.get("SUPABASE_URL", ""), env.get("SUPABASE_SERVICE_ROLE_KEY", ""), **kwargs)

    def close(self) -> None:
        self._client.close()

    def save_trades(self, rows: Sequence[Mapping[str, Any]]) -> None:
        if rows:
            self._post(
                "/reference_trades",
                [dict(r) for r in rows],
                on_conflict="strategy_id,symbol,closed_at",
                resolution="ignore-duplicates",
            )

    def upsert_equity(self, points: Sequence[EquityPoint]) -> None:
        if points:
            self._post(
                "/reference_equity",
                [p.to_row() for p in points],
                on_conflict="strategy_id,day",
                resolution="merge-duplicates",
            )

    def _post(self, path: str, rows: list[dict[str, Any]], *, on_conflict: str, resolution: str):
        headers = {**self._headers, "Prefer": f"resolution={resolution},return=minimal"}
        try:
            resp = self._client.post(
                self._base + path, params={"on_conflict": on_conflict}, json=rows, headers=headers
            )
        except httpx.HTTPError as exc:
            raise ReferenceStoreError(f"no se pudo llamar a PostgREST: {exc}") from exc
        if not resp.is_success:
            raise ReferenceStoreError(
                f"PostgREST respondió {resp.status_code} en {path}: {resp.text[:300]}"
            )
