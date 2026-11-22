"""Persistencia del ledger de referencia: ``reference_trades`` y ``reference_equity``.

Las tablas están en la migración ``apps/api/migrations/0003_reference_ledger.sql`` (D1). Las
escribe solo el motor con el token de servicio (``/internal/reference/*``); cualquier usuario
autenticado las lee (página ``/strategies``, #58). Hay dos backends:
:class:`InMemoryReferenceStore` (pruebas) y :class:`WorkerApiReferenceStore` (API en Workers).

Las escrituras son idempotentes: los trades tienen llave única ``(strategy_id, symbol,
closed_at)`` y se insertan ignorando duplicados, y la equity diaria hace *upsert* sobre
``(strategy_id, day)``. Así se pueden reintentar sin duplicar filas.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol
from uuid import UUID

from tm_shared.api_client import WorkerApiClient, WorkerApiError

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


#: Filas por petición: el Worker acepta hasta 500 por batch de D1.
CHUNK = 500


class WorkerApiReferenceStore:
    """Backend sobre la API en Workers: ``POST /internal/reference/trades`` (ignora
    duplicados) y ``PUT /internal/reference/equity`` (upsert por ``strategy_id, day``)."""

    def __init__(self, client: WorkerApiClient, *, chunk: int = CHUNK) -> None:
        self.client = client
        self.chunk = chunk

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, **kwargs: Any
    ) -> WorkerApiReferenceStore:
        """Lee ``TM_API_URL``, ``TM_SERVICE_TOKEN`` y los headers de Access del entorno."""
        return cls(WorkerApiClient.from_env(env), **kwargs)

    def close(self) -> None:
        self.client.close()

    def save_trades(self, rows: Sequence[Mapping[str, Any]]) -> None:
        for i in range(0, len(rows), self.chunk):
            batch = [dict(r) for r in rows[i : i + self.chunk]]
            # Idempotente: la API ignora los duplicados por (strategy_id, symbol, closed_at).
            self._call("POST", "/internal/reference/trades", {"rows": batch}, idempotent=True)

    def upsert_equity(self, points: Sequence[EquityPoint]) -> None:
        for i in range(0, len(points), self.chunk):
            batch = [p.to_row() for p in points[i : i + self.chunk]]
            self._call("PUT", "/internal/reference/equity", {"points": batch})

    def _call(self, method: str, path: str, body: Any, **kw: Any) -> Any:
        try:
            return self.client.request(method, path, json=body, **kw)
        except WorkerApiError as exc:
            raise ReferenceStoreError(str(exc)) from exc
