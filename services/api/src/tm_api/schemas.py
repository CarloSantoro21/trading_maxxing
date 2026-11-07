"""Modelos de entrada y salida de la API.

Las validaciones replican los checks de `supabase/migrations/20261107000100_tables.sql`
para responder 422 con un mensaje claro antes de llegar a la base.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from tm_shared.contracts import Horizon, OrderSide, Timeframe

Symbol = Literal["BTCUSDT", "ETHUSDT"]
SUPPORTED_SYMBOLS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")

RiskLevel = Annotated[int, Field(ge=1, le=5, description="Tolerancia al riesgo, de 1 a 5")]
Capital = Annotated[Decimal, Field(gt=0, description="Capital en USDT, mayor que 0")]
MaxDrawdown = Annotated[
    Decimal,
    Field(gt=0, lt=1, description="Drawdown máximo como fracción en (0, 1): 0.2 = 20 %"),
]
TargetReturn = Annotated[
    Decimal, Field(gt=0, description="Retorno objetivo como fracción: 0.15 = 15 %")
]


class BotStatus(StrEnum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"
    KILLED = "killed"


class BacktestStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _unique_symbols(value: list[str] | None) -> list[str] | None:
    if value is not None and len(set(value)) != len(value):
        raise ValueError("los instrumentos no deben repetirse")
    return value


def _require_some_field(model: BaseModel) -> None:
    """En un PATCH: al menos un campo, y ninguno explícitamente en null."""
    if not model.model_fields_set:
        raise ValueError("envía al menos un campo para actualizar")
    for name in model.model_fields_set:
        if getattr(model, name) is None:
            raise ValueError(f"{name} no puede ser null")


def _strip_name(value: str | None) -> str | None:
    if value is None:
        return value
    value = value.strip()
    if not value:
        raise ValueError("el nombre no puede estar vacío")
    return value


# --- perfiles ----------------------------------------------------------------


class ProfileCreate(_Input):
    horizon: Horizon
    risk_level: RiskLevel
    capital: Capital
    max_drawdown: MaxDrawdown
    instruments: list[Symbol] = Field(default_factory=lambda: list(SUPPORTED_SYMBOLS), min_length=1)

    _check_instruments = field_validator("instruments")(_unique_symbols)

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "horizon": "medium",
                    "risk_level": 3,
                    "capital": "10000",
                    "max_drawdown": "0.2",
                    "instruments": ["BTCUSDT", "ETHUSDT"],
                }
            ]
        },
    )


class ProfileUpdate(_Input):
    horizon: Horizon | None = None
    risk_level: RiskLevel | None = None
    capital: Capital | None = None
    max_drawdown: MaxDrawdown | None = None
    instruments: list[Symbol] | None = Field(default=None, min_length=1)

    _check_instruments = field_validator("instruments")(_unique_symbols)

    @model_validator(mode="after")
    def _not_empty(self) -> ProfileUpdate:
        _require_some_field(self)
        return self

    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"examples": [{"risk_level": 4, "capital": "15000"}]}
    )


class Profile(BaseModel):
    id: UUID
    user_id: UUID
    horizon: Horizon
    risk_level: int
    capital: float
    max_drawdown: float
    instruments: list[str]
    created_at: datetime
    updated_at: datetime


# --- metas ---------------------------------------------------------------------


class GoalCreate(_Input):
    profile_id: UUID
    target_return: TargetReturn
    deadline: date = Field(description="Fecha límite; no puede estar en el pasado")

    @field_validator("deadline")
    @classmethod
    def _not_past(cls, value: date) -> date:
        if value < datetime.now(UTC).date():
            raise ValueError("la fecha límite no puede estar en el pasado")
        return value

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "profile_id": "7d1c3f0e-3a51-4c8e-9a51-2a8f0c6f1b11",
                    "target_return": "0.15",
                    "deadline": "2027-06-30",
                }
            ]
        },
    )


class GoalUpdate(_Input):
    target_return: TargetReturn | None = None
    deadline: date | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> GoalUpdate:
        _require_some_field(self)
        return self


class Goal(BaseModel):
    id: UUID
    profile_id: UUID
    target_return: float
    deadline: date
    created_at: datetime
    updated_at: datetime


# --- bots ----------------------------------------------------------------------


class BotCreate(_Input):
    name: str = Field(min_length=1, max_length=100)
    profile_id: UUID | None = Field(
        default=None, description="Perfil dueño; si se omite se usa el perfil del usuario"
    )
    strategy_config: dict[str, Any] = Field(
        default_factory=dict,
        description="Estrategias activas y su configuración por sleeve",
        examples=[{"trend": {"enabled": True, "weight": 0.5}}],
    )

    _check_name = field_validator("name")(_strip_name)


class BotUpdate(_Input):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    strategy_config: dict[str, Any] | None = None

    _check_name = field_validator("name")(_strip_name)

    @model_validator(mode="after")
    def _not_empty(self) -> BotUpdate:
        _require_some_field(self)
        return self

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"strategy_config": {"trend": {"enabled": False}}}]},
    )


class Bot(BaseModel):
    id: UUID
    profile_id: UUID
    name: str
    status: BotStatus
    strategy_config: dict[str, Any]
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class KillRequest(_Input):
    reason: str = Field(
        default="paro de emergencia manual", max_length=500, description="Motivo del paro"
    )


class BotCommandResult(BaseModel):
    bot: Bot
    command: Literal["start", "stop", "kill"]
    command_id: str = Field(description="ID de la entrada en el stream tm:commands")


class KillAllResult(BaseModel):
    killed: list[BotCommandResult]


# --- trades y portafolio ------------------------------------------------------


class Trade(BaseModel):
    id: UUID
    bot_id: UUID
    symbol: str
    side: OrderSide
    quantity: float
    entry_price: float
    exit_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    fees: float
    pnl: float | None = None
    rationale: dict[str, Any] = Field(default_factory=dict)
    opened_at: datetime
    closed_at: datetime | None = None


class TradePage(BaseModel):
    items: list[Trade]
    total: int
    limit: int
    offset: int


class Sleeve(BaseModel):
    """Foto de un bot: su último equity, sus posiciones abiertas y su P&L realizado."""

    bot_id: UUID
    name: str
    status: BotStatus
    equity: float | None = None
    cash: float | None = None
    positions_value: float | None = None
    drawdown: float | None = None
    as_of: datetime | None = None
    realized_pnl: float = 0.0
    open_trades: int = 0


class Portfolio(BaseModel):
    profile_id: UUID
    capital: float
    max_drawdown: float
    equity: float
    cash: float
    positions_value: float
    realized_pnl: float
    total_return: float = Field(description="(equity - capital) / capital")
    sleeves: list[Sleeve]
    open_positions: list[Trade]


# --- backtests -----------------------------------------------------------------


class BacktestConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    symbols: list[Symbol] = Field(min_length=1)
    timeframe: Timeframe = Timeframe.M15
    start: date
    end: date
    mode: Literal["in_sample", "out_of_sample", "walk_forward"] = "walk_forward"
    strategies: list[str] = Field(default_factory=list, description="Vacío = todas")
    initial_capital: Capital | None = None

    _check_symbols = field_validator("symbols")(_unique_symbols)

    @model_validator(mode="after")
    def _dates(self) -> BacktestConfig:
        if self.end <= self.start:
            raise ValueError("end debe ser posterior a start")
        return self


class BacktestCreate(_Input):
    profile_id: UUID | None = Field(
        default=None, description="Perfil; si se omite se usa el perfil del usuario"
    )
    config: BacktestConfig

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "config": {
                        "symbols": ["BTCUSDT"],
                        "timeframe": "15m",
                        "start": "2021-01-01",
                        "end": "2024-12-31",
                        "mode": "walk_forward",
                        "strategies": ["trend"],
                    }
                }
            ]
        },
    )


class Backtest(BaseModel):
    id: UUID
    profile_id: UUID
    status: BacktestStatus
    config: dict[str, Any]
    report: dict[str, Any] | None = None
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
