"""Modelos de entrada y salida de la API.

Las validaciones replican los checks de `supabase/migrations/` (capital mínimo, nivel de
riesgo, etc.) para responder 422 con un mensaje claro antes de llegar a la base.
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

#: Capital mínimo de un perfil en USDT de testnet (igual que el check profiles_capital_min y
#: que tm_agents.risk.MIN_CAPITAL).
MIN_CAPITAL = Decimal("500")

RiskLevel = Annotated[
    int,
    Field(
        ge=1,
        le=5,
        description=(
            "Nivel de riesgo de 1 a 5. Define el drawdown máximo (10, 15, 20, 30 o 40 %), "
            "el riesgo por operación y la exposición máxima (tm_agents.risk.RISK_TABLE)"
        ),
    ),
]
Capital = Annotated[
    Decimal, Field(ge=MIN_CAPITAL, description="Capital en USDT de testnet, mínimo 500")
]
PositiveAmount = Annotated[Decimal, Field(gt=0, description="Monto en USDT, mayor que 0")]
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
    """Lo único que captura el onboarding: capital y nivel de riesgo.

    `max_drawdown` lo deriva la base del nivel; `horizon` e `instruments` toman sus valores
    por defecto (medium; BTCUSDT y ETHUSDT). Mandarlos da 422.
    """

    risk_level: RiskLevel
    capital: Capital

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"risk_level": 3, "capital": "10000"}]},
    )


class ProfileUpdate(_Input):
    risk_level: RiskLevel | None = None
    capital: Capital | None = None

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
    max_drawdown: float = Field(description="Derivado de risk_level por la base")
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
    initial_capital: PositiveAmount | None = None

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


# --- usuario actual y roles -----------------------------------------------------


class AppRole(StrEnum):
    """Rol en el equipo (igual al enum public.app_role)."""

    ADMIN = "admin"
    MEMBER = "member"


class Me(BaseModel):
    id: UUID
    email: str | None = None
    role: AppRole = Field(description="Sin fila en app_roles, el usuario es member")


# --- estrategias (ciclo de vida) ------------------------------------------------


class StrategyState(StrEnum):
    """Igual al enum public.strategy_state y a tm_agents.strategy_stats.StrategyState."""

    RESEARCH = "research"
    LAB = "lab"
    SHADOW = "shadow"
    ACTIVE = "active"
    PROBATION = "probation"
    RETIRED = "retired"


class Strategy(BaseModel):
    id: UUID
    name: str
    state: StrategyState
    params: dict[str, Any] = Field(default_factory=dict)
    lab_report: dict[str, Any] | None = None
    live_metrics: dict[str, Any] = Field(default_factory=dict)
    state_changed_at: datetime
    created_at: datetime
    updated_at: datetime


class StrategyTransitionRequest(_Input):
    reason: str = Field(
        min_length=1, max_length=1000, description="Por qué se promueve o se retira"
    )

    @field_validator("reason")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("el motivo no puede estar vacío")
        return value


class StrategyTransition(BaseModel):
    strategy: Strategy
    from_state: StrategyState
    to_state: StrategyState
    actor: str
    reason: str


# --- alta de usuarios (solo admin) ----------------------------------------------


class UserCreate(_Input):
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: AppRole = AppRole.MEMBER

    @field_validator("email", mode="before")
    @classmethod
    def _normalize(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"email": "ana@equipo.dev", "role": "member"}]},
    )


class CreatedUser(BaseModel):
    id: UUID
    email: str
    role: AppRole
    temporary_password: str = Field(
        description="Contraseña temporal; se muestra una sola vez y la persona debe cambiarla"
    )


# --- probabilidad de alcanzar una meta (Monte Carlo #3, #38) ----------------------------


class GoalFanPoint(BaseModel):
    day: date
    p10: float
    p50: float
    p90: float


class GoalStrategyContribution(BaseModel):
    name: str
    state: str
    source: Literal["reference", "lab_report", "none"] = Field(
        description="De dónde salieron los retornos: portafolio de referencia o laboratorio"
    )
    report_source: Literal["db", "file", "none"] = Field(
        description="Reporte del laboratorio usado: el de la tabla strategies o el archivo"
    )
    observations: int = Field(description="Retornos diarios usados")
    weight: float = Field(description="Peso del sleeve (PortfolioAllocator)")
    exposure: float = Field(description="Exposición del nivel de riesgo (0 a 1)")
    excluded: str | None = Field(default=None, description="Por qué no recibe capital")


class GoalProbability(BaseModel):
    goal_id: UUID
    profile_id: UUID
    target_return: float
    deadline: date
    as_of: date
    days: int = Field(description="Días que faltan para la fecha límite")
    capital: float = Field(description="Capital del perfil (base del retorno objetivo)")
    start_equity: float = Field(description="Equity actual del usuario")
    target_equity: float = Field(description="capital * (1 + target_return)")
    probability: float = Field(
        description="P(el equity toca la meta en algún día antes de la fecha límite)"
    )
    probability_at_deadline: float = Field(description="P(equity >= meta en la fecha límite)")
    invested_fraction: float = Field(description="Suma de peso * exposición; el resto en USDT")
    final_equity_p10: float
    final_equity_p50: float
    final_equity_p90: float
    n_simulations: int
    block_size: int
    seed: int
    fan: list[GoalFanPoint] = Field(description="Abanico p10/p50/p90 del equity por día")
    strategies: list[GoalStrategyContribution]
    notes: list[str] = Field(default_factory=list)


# --- reportes del laboratorio (#24) ---------------------------------------------------------


class LabReportSummary(BaseModel):
    name: str = Field(description="Nombre del archivo del reporte, sin .json")
    strategy: str
    symbols: list[str]
    oos_start: str | None = None
    oos_end: str | None = None
    aggregate: dict[str, Any] = Field(description="Métricas out-of-sample agregadas")
    buy_and_hold: dict[str, Any] = Field(description="Comprar y mantener en el mismo periodo")
    max_drawdown_p95: float | None = Field(default=None, description="Drawdown p95 del MC")
    approved: bool
    reasons: list[str] = Field(description="Razones de cada criterio que falla")


class LabEquityPoint(BaseModel):
    day: str
    strategy: float
    buy_and_hold: float | None = None


class LabWindow(BaseModel):
    label: str | None = None
    is_start: str | None = None
    is_end: str | None = None
    oos_start: str | None = None
    oos_end: str | None = None
    params_label: str | None = None
    is_sharpe: float | None = None
    oos_return: float | None = None
    oos_sharpe: float | None = None
    oos_max_drawdown: float | None = None
    hold_return: float | None = None
    trades: int | None = None


class HistogramBin(BaseModel):
    start: float
    end: float
    count: int


class DrawdownHistogram(BaseModel):
    bins: list[HistogramBin]
    p95: float = Field(description="p95 del reporte")
    p95_recomputed: float = Field(description="p95 de las muestras de este histograma")
    p50: float
    n_simulations: int
    block_size: int
    seed: int
    horizon_days: int
    limit: float | None = Field(default=None, description="Tope del criterio de aprobación")


class LabReportDetail(LabReportSummary):
    config: dict[str, Any]
    per_symbol: dict[str, Any]
    parameter_stability: dict[str, Any]
    montecarlo: dict[str, Any]
    checks: list[dict[str, Any]]
    exposure_by_risk_level: dict[str, float]
    windows: list[LabWindow]
    equity: list[LabEquityPoint]
    drawdown_histogram: DrawdownHistogram | None = None
