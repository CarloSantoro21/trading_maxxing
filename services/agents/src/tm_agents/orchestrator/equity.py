"""Equity de cada bot hacia la API: ``POST /internal/equity-snapshots`` cada pocos minutos.

El dashboard (``GET /api/dashboard`` y ``GET /api/equity-snapshots?since=``) dibuja la curva
de equity con estas fotos. Salen del libro virtual de cada ``BotRunner`` (efectivo, posiciones
a mercado y drawdown contra el pico), el mismo con el que el RiskAgent decide.

Cada foto lleva ``ts`` redondeado al intervalo, así que un reintento hace *upsert* sobre la
misma ``(bot_id, ts)`` en vez de duplicar. Nunca lanza: si la API no responde se registra y
se intenta en el siguiente intervalo.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from tm_shared.api_client import WorkerApiClient, WorkerApiError

log = logging.getLogger(__name__)

#: El Worker acepta hasta 500 fotos por petición.
MAX_BATCH = 500


class EquitySnapshotReporter:
    """Manda la equity de los runners del supervisor cada ``interval`` segundos."""

    def __init__(
        self,
        client: WorkerApiClient,
        supervisor: Any,
        *,
        interval: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.supervisor = supervisor
        self.interval = interval
        self._clock = clock
        self._last = float("-inf")
        self.sent = 0
        self.failed = 0

    def maybe_report(self, now: datetime | None = None) -> int:
        if self._clock() - self._last < self.interval:
            return 0
        self._last = self._clock()
        return self.report(now)

    def snapshots(self, now: datetime | None = None) -> list[dict[str, Any]]:
        now = (now or datetime.now(UTC)).astimezone(UTC)
        step = max(1, int(self.interval))
        ts = datetime.fromtimestamp(int(now.timestamp()) // step * step, UTC)
        out = []
        for bot_id, runner in sorted(self.supervisor.runners.items()):
            if not _is_uuid(bot_id):
                continue  # bots de prueba o de desarrollo sin fila en D1
            book = runner.book
            equity = book.equity()
            peak = max(book.peak_equity, equity)
            drawdown = 0.0 if peak <= 0 else float(max(Decimal(0), 1 - equity / peak))
            out.append(
                {
                    "bot_id": bot_id,
                    "ts": ts.isoformat(),
                    "equity": float(max(equity, Decimal(0))),
                    "cash": float(max(book.cash, Decimal(0))),
                    "positions_value": float(book.exposure()),
                    "drawdown": min(1.0, drawdown),
                }
            )
        return out

    def report(self, now: datetime | None = None) -> int:
        rows = self.snapshots(now)
        sent = 0
        for i in range(0, len(rows), MAX_BATCH):
            batch = rows[i : i + MAX_BATCH]
            try:
                # Upsert por (bot_id, ts): repetirlo no duplica.
                self.client.post(
                    "/internal/equity-snapshots", {"snapshots": batch}, idempotent=True
                )
            except WorkerApiError as exc:
                self.failed += 1
                log.warning("no se pudo guardar la equity de %d bots: %s", len(batch), exc)
                continue
            sent += len(batch)
        self.sent += sent
        return sent


def _is_uuid(value: str) -> bool:
    try:
        UUID(str(value))
    except ValueError:
        return False
    return True


__all__ = ["EquitySnapshotReporter"]
