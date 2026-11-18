"""Persistencia de las auditorías del StrategyAuditorAgent (#108).

La tabla ``public.signal_audits`` vive en la migración ``20261118000000_signal_audits.sql``.
Solo el backend (``service_role``) escribe; el dueño del bot lee sus filas (RLS con
``private.owns_bot``). Hay dos backends: :class:`InMemorySignalAuditStore` (pruebas) y
:class:`PostgRESTSignalAuditStore` (Supabase con la llave ``service_role``).

Las escrituras son idempotentes: cada auditoría tiene llave única
``(bot_id, strategy, symbol, mode, close_ts, trade_id)``; el *upsert* usa
``Prefer: resolution=merge-duplicates`` para reemplazar la fila si se reintenta.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

import httpx

from tm_agents.strategy_auditor.models import Mode, SignalAudit

_ON_CONFLICT = "bot_id,strategy,symbol,mode,close_ts,trade_id"


class SignalAuditStoreError(Exception):
    """No se pudo guardar o leer la auditoría."""


class SignalAuditStore(Protocol):
    """Dónde se guardan las auditorías del StrategyAuditorAgent."""

    def put(self, audit: SignalAudit) -> None:
        """Guarda la auditoría; si ya existe una con la misma llave, la reemplaza."""
        ...

    def get_by_key(
        self,
        bot_id: UUID,
        strategy: str,
        symbol: str,
        mode: Mode,
        close_ts: datetime,
        trade_id: UUID | None,
    ) -> SignalAudit | None:
        """Devuelve la auditoría con esa llave única, o ``None`` si no existe."""
        ...


class InMemorySignalAuditStore:
    """Backend en memoria con la misma llave única que la base. Para pruebas."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, str, str, str, str | None], SignalAudit] = {}

    def put(self, audit: SignalAudit) -> None:
        self._rows[audit.unique_key()] = audit

    def get_by_key(
        self,
        bot_id: UUID,
        strategy: str,
        symbol: str,
        mode: Mode,
        close_ts: datetime,
        trade_id: UUID | None,
    ) -> SignalAudit | None:
        key = (
            str(bot_id),
            strategy,
            symbol,
            mode,
            close_ts.isoformat(),
            None if trade_id is None else str(trade_id),
        )
        return self._rows.get(key)

    def all(self) -> list[SignalAudit]:
        """Todas las auditorías guardadas, ordenadas por ``close_ts`` ascendente."""
        return sorted(self._rows.values(), key=lambda a: a.close_ts)


class PostgRESTSignalAuditStore:
    """Backend sobre PostgREST de Supabase con la llave ``service_role``.

    Hace *upsert* por llave única con ``Prefer: resolution=merge-duplicates``. La lectura por
    llave filtra por los seis campos y pide una sola fila.
    """

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
        # Las llaves nuevas (sb_secret_...) no son JWT y solo van en el header apikey.
        if key.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {key}"
        self._base = url.rstrip("/") + "/rest/v1"
        self._client = client or httpx.Client(timeout=timeout)
        self._headers = headers

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, **kwargs: Any
    ) -> PostgRESTSignalAuditStore:
        """Lee ``SUPABASE_URL`` y ``SUPABASE_SERVICE_ROLE_KEY`` del entorno."""
        env = os.environ if env is None else env
        return cls(env.get("SUPABASE_URL", ""), env.get("SUPABASE_SERVICE_ROLE_KEY", ""), **kwargs)

    def close(self) -> None:
        self._client.close()

    def put(self, audit: SignalAudit) -> None:
        headers = {**self._headers, "Prefer": "resolution=merge-duplicates,return=minimal"}
        try:
            resp = self._client.post(
                self._base + "/signal_audits",
                params={"on_conflict": _ON_CONFLICT},
                json=[audit.to_row()],
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise SignalAuditStoreError(f"no se pudo llamar a PostgREST: {exc}") from exc
        if not resp.is_success:
            raise SignalAuditStoreError(
                f"PostgREST respondió {resp.status_code} en /signal_audits: {resp.text[:300]}"
            )

    def get_by_key(
        self,
        bot_id: UUID,
        strategy: str,
        symbol: str,
        mode: Mode,
        close_ts: datetime,
        trade_id: UUID | None,
    ) -> SignalAudit | None:
        params: dict[str, str] = {
            "select": "*",
            "bot_id": f"eq.{bot_id}",
            "strategy": f"eq.{strategy}",
            "symbol": f"eq.{symbol}",
            "mode": f"eq.{mode}",
            "close_ts": f"eq.{close_ts.isoformat()}",
            "trade_id": "is.null" if trade_id is None else f"eq.{trade_id}",
            "limit": "1",
        }
        try:
            resp = self._client.get(
                self._base + "/signal_audits", params=params, headers=self._headers
            )
        except httpx.HTTPError as exc:
            raise SignalAuditStoreError(f"no se pudo llamar a PostgREST: {exc}") from exc
        if not resp.is_success:
            raise SignalAuditStoreError(
                f"PostgREST respondió {resp.status_code} en /signal_audits: {resp.text[:300]}"
            )
        rows = resp.json() if resp.content else []
        if not rows:
            return None
        return _audit_from_row(rows[0])


def _audit_from_row(row: Mapping[str, Any]) -> SignalAudit:
    """Reconstruye una :class:`SignalAudit` a partir de una fila de PostgREST."""
    return SignalAudit(
        id=UUID(str(row["id"])),
        bot_id=UUID(str(row["bot_id"])),
        trade_id=None if row.get("trade_id") in (None, "") else UUID(str(row["trade_id"])),
        strategy=row["strategy"],
        symbol=row["symbol"],
        close_ts=_parse_ts(row["close_ts"]),
        mode=row["mode"],
        decision=row["decision"],
        veto_reason=row.get("veto_reason"),
        confidence=int(row["confidence"]),
        rule_evaluations=row.get("rule_evaluations") or {},
        input=row.get("input") or {},
        output=row.get("output") or {},
        trace_id=row.get("trace_id"),
        status=row.get("status", "ok"),
        latency_ms=row.get("latency_ms"),
        model=row.get("model"),
        created_at=None if row.get("created_at") is None else _parse_ts(row["created_at"]),
    )


def _parse_ts(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    # PostgREST devuelve ISO 8601; Python 3.12 entiende "+00:00" y "Z".
    return datetime.fromisoformat(value)
