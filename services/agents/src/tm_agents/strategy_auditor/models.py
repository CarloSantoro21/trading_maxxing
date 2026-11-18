"""Modelo Pydantic de una auditoría del StrategyAuditorAgent (#108).

El modelo refleja exactamente la tabla ``public.signal_audits`` de la migración
``20261118000000_signal_audits.sql``: los mismos nombres, los mismos tipos y las mismas
validaciones (``mode``, ``decision``, ``status``, ``confidence``, ``latency_ms``, objetos
JSON). La clave única es ``(bot_id, strategy, symbol, mode, close_ts, trade_id)``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

Mode = Literal["entry", "exit"]
Decision = Literal["APPROVE", "REJECT", "HOLD", "CLOSE_NOW"]
Status = Literal["ok", "fail_open", "timeout", "error"]


class SignalAudit(BaseModel):
    """Una fila de ``signal_audits``.

    Attributes:
        bot_id: bot al que pertenece la auditoría.
        trade_id: trade asociado (si aplica); nulo si todavía no se abrió.
        strategy: estrategia que emitió la señal (``trend``, ``kronos``, ...).
        symbol: par o símbolo evaluado (``BTCUSDT``, ...).
        close_ts: vela cerrada que dispara la auditoría.
        mode: ``entry`` antes de abrir el trade o ``exit`` mientras está abierto.
        decision: veredicto del auditor.
        veto_reason: razón del veto (``REJECT`` o ``CLOSE_NOW``), si la hay.
        confidence: confianza [0, 100] del auditor.
        rule_evaluations: resultado de cada regla aplicada (objeto JSON).
        input: contexto de entrada que vio el auditor (objeto JSON).
        output: salida completa del auditor (objeto JSON).
        trace_id: ID del trace en Langfuse para abrir el detalle.
        status: ``ok`` / ``fail_open`` / ``timeout`` / ``error``.
        latency_ms: latencia de la evaluación en milisegundos.
        model: modelo usado por el auditor (si lo hubo).
        id: identificador local; en la base la pone ``gen_random_uuid()``.
        created_at: cuándo se registró; en la base la pone ``now()``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    bot_id: UUID
    trade_id: UUID | None = None
    strategy: str = Field(min_length=1, max_length=64)
    symbol: str = Field(min_length=1, max_length=32)
    close_ts: datetime
    mode: Mode
    decision: Decision
    veto_reason: str | None = None
    confidence: int = Field(ge=0, le=100)
    rule_evaluations: dict[str, Any] = Field(default_factory=dict)
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None
    status: Status = "ok"
    latency_ms: int | None = Field(default=None, ge=0)
    model: str | None = None
    id: UUID = Field(default_factory=uuid4)
    created_at: datetime | None = None

    def unique_key(self) -> tuple[str, str, str, str, str, str | None]:
        """Llave única de la fila: ``(bot_id, strategy, symbol, mode, close_ts, trade_id)``."""
        return (
            str(self.bot_id),
            self.strategy,
            self.symbol,
            self.mode,
            self.close_ts.isoformat(),
            None if self.trade_id is None else str(self.trade_id),
        )

    def to_row(self) -> dict[str, Any]:
        """Serializa a la fila JSON que espera PostgREST."""
        return {
            "id": str(self.id),
            "bot_id": str(self.bot_id),
            "trade_id": None if self.trade_id is None else str(self.trade_id),
            "strategy": self.strategy,
            "symbol": self.symbol,
            "close_ts": self.close_ts.isoformat(),
            "mode": self.mode,
            "decision": self.decision,
            "veto_reason": self.veto_reason,
            "confidence": self.confidence,
            "rule_evaluations": dict(self.rule_evaluations),
            "input": dict(self.input),
            "output": dict(self.output),
            "trace_id": self.trace_id,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "model": self.model,
        }
