"""Contratos compartidos entre estrategias, orquestador, riesgo y motor.

Todos los modelos son Pydantic, inmutables y serializables a JSON, para viajar por Redis
y guardarse en Supabase (`trades.rationale`). Los valores de los enums coinciden con los
tipos de la base de datos (ver `supabase/migrations`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Horizon(StrEnum):
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


class Direction(StrEnum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class Timeframe(StrEnum):
    M1 = "1m"
    M15 = "15m"
    M30 = "30m"
    D1 = "1d"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Signal(_Frozen):
    """Opinión de una estrategia sobre un símbolo en un momento dado."""

    symbol: str = Field(pattern=r"^[A-Z0-9]{5,20}$")
    direction: Direction
    confidence: float = Field(ge=0.0, le=1.0)
    horizon: Horizon
    timeframe: Timeframe
    source: str = Field(min_length=1, description="Nombre de la estrategia que la emitió")
    generated_at: datetime = Field(default_factory=_utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("generated_at")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("generated_at debe tener zona horaria (UTC)")
        return v.astimezone(UTC)

    @property
    def score(self) -> float:
        """Señal con signo en [-1, 1]: +conf si es long, -conf si es short, 0 si es flat."""
        sign = {Direction.LONG: 1.0, Direction.SHORT: -1.0, Direction.FLAT: 0.0}[self.direction]
        return sign * self.confidence


class SimResult(_Frozen):
    """Resultado del Monte Carlo previo a una operación (SimulationGate)."""

    p_take_profit: float = Field(ge=0.0, le=1.0)
    p_stop_loss: float = Field(ge=0.0, le=1.0)
    p_open: float = Field(ge=0.0, le=1.0, description="Sigue abierta al final del horizonte")
    expected_value: float = Field(description="Valor esperado en unidades de R (riesgo)")
    n_paths: int = Field(gt=0)
    source: str = Field(description="kronos, bootstrap o combined")

    @model_validator(mode="after")
    def _probabilities_sum_to_one(self) -> SimResult:
        total = self.p_take_profit + self.p_stop_loss + self.p_open
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"las probabilidades deben sumar 1 (suman {total:.6f})")
        return self


class RiskVerdict(_Frozen):
    approved: bool
    reasons: list[str] = Field(default_factory=list)


class Rationale(_Frozen):
    """Justificación estructurada de una decisión; se guarda tal cual en `trades.rationale`."""

    signals: list[Signal] = Field(default_factory=list)
    weights: dict[str, float] = Field(default_factory=dict)
    news_veto: bool = False
    news_summary: str | None = None
    sim: SimResult | None = None
    risk: RiskVerdict | None = None
    text: str | None = Field(default=None, description="Explicación del ExplainerAgent")


class TradeDecision(_Frozen):
    """Orden propuesta por el orquestador y validada por riesgo, lista para el motor."""

    symbol: str = Field(pattern=r"^[A-Z0-9]{5,20}$")
    side: OrderSide
    quantity: Decimal = Field(gt=0)
    entry_price: Decimal | None = Field(default=None, gt=0)
    stop_loss: Decimal = Field(gt=0)
    take_profit: Decimal = Field(gt=0)
    rationale: Rationale = Field(default_factory=Rationale)
    decided_at: datetime = Field(default_factory=_utcnow)

    @model_validator(mode="after")
    def _levels_consistent(self) -> TradeDecision:
        ref = self.entry_price
        if self.side is OrderSide.BUY:
            if not self.stop_loss < self.take_profit:
                raise ValueError("en una compra el stop_loss debe quedar debajo del take_profit")
            if ref is not None and not self.stop_loss < ref < self.take_profit:
                raise ValueError("en una compra: stop_loss < entry_price < take_profit")
        else:
            if not self.take_profit < self.stop_loss:
                raise ValueError("en una venta el take_profit debe quedar debajo del stop_loss")
            if ref is not None and not self.take_profit < ref < self.stop_loss:
                raise ValueError("en una venta: take_profit < entry_price < stop_loss")
        return self

    @property
    def risk_per_unit(self) -> Decimal | None:
        """Distancia entre la entrada y el stop (1R por unidad), si se conoce la entrada."""
        if self.entry_price is None:
            return None
        return abs(self.entry_price - self.stop_loss)
