"""Eventos del orquestador: lo que la capa compartida publica en `tm:signals`.

Formato del stream (un solo campo `data` con JSON, igual que `tm:decisions`)::

    XADD tm:signals MAXLEN ~ 10000 * data '<SignalEvent en JSON>'

Un `SignalEvent` por estrategia (activa, en observación o en shadow) y símbolo en cada cierre
de vela del timeframe de la estrategia::

    {
      "id": "trend:BTCUSDT:2024-03-01T00:00:00Z",   # estrategia:símbolo:cierre (idempotencia)
      "strategy": "trend",
      "state": "active",                            # active | probation | shadow
      "symbol": "BTCUSDT",
      "bar_close": "2024-03-01T00:00:00Z",          # cierre de la vela que produjo la señal
      "price": "61234.5",                           # último cierre (referencia de tamaño)
      "signal": {...Signal},                        # dirección, confianza, metadata
      "veto": null | {"scope": "BTC", "impact": "high", "reason": "...", "expires_at": "..."},
      "sim": null | {...SimResult},                 # SimulationGate (solo si la señal es long)
      "sim_error": null | "historia insuficiente ...",
      "stop_loss": "3061.72", "take_profit": "489876.0",
      "hard_stop": false,                           # true = stop real (Kronos); false = catástrofe
      "catalog": [{...StrategySnapshot}, ...]       # estado y estadísticas de todas las vivas
    }

`catalog` viaja en cada evento para que cada bot calcule sus pesos (PortfolioAllocator) y su
exposición por nivel sin volver a leer el registro: el evento basta para reproducir la
decisión del bot.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from tm_shared.contracts import Horizon, Signal, SimResult

from tm_agents.news.models import Veto
from tm_agents.orchestrator.exposure import KNOWN_SENSITIVITY
from tm_agents.registry import StrategyRecord
from tm_agents.strategy_stats import StrategyState, StrategyStats

#: Estados que reciben eventos: los que operan capital y los que corren en shadow.
LIVE_STATES: tuple[StrategyState, ...] = (
    StrategyState.ACTIVE,
    StrategyState.PROBATION,
    StrategyState.SHADOW,
)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class StrategySnapshot(_Frozen):
    """Lo que un bot necesita saber de una estrategia para dimensionar su sleeve.

    Sale de la fila de ``strategies`` (estado) y de su ``lab_report`` (lo que midió el
    laboratorio). Una estrategia sin reporte llega con volatilidad 0 y el PortfolioAllocator
    no le da capital.
    """

    name: str
    state: StrategyState
    volatility: float = Field(ge=0.0)
    oos_sharpe: float
    horizon: Horizon | None = None
    max_drawdown_p95: float | None = Field(default=None, ge=0.0, le=1.0)
    #: Curva de sensibilidad del laboratorio: pares ``(exposición, drawdown p95)``. Sale del
    #: reporte (``exposure_sensitivity``) o de la curva medida para su variante.
    sensitivity: tuple[tuple[float, float], ...] | None = None

    @classmethod
    def from_record(cls, record: StrategyRecord) -> StrategySnapshot:
        report: Mapping[str, Any] = record.lab_report or {}
        aggregate = report.get("aggregate") or {}
        mc = (report.get("montecarlo") or {}).get("max_drawdown") or {}
        horizon = report.get("horizon")
        # La curva del reporte; si no trae, la medida para su variante (config.strategy).
        lab_name = (report.get("config") or {}).get("strategy") or record.name
        sensitivity = report.get("exposure_sensitivity") or KNOWN_SENSITIVITY.get(lab_name)
        return cls(
            name=record.name,
            state=record.state,
            volatility=_finite(aggregate.get("volatility"), 0.0),
            oos_sharpe=_finite(aggregate.get("sharpe"), 0.0),
            horizon=Horizon(horizon) if horizon else None,
            max_drawdown_p95=_finite(mc.get("p95"), None),
            sensitivity=tuple((float(k), float(d)) for k, d in sensitivity)
            if sensitivity
            else None,
        )

    def to_stats(self, *, state: StrategyState | None = None) -> StrategyStats:
        return StrategyStats(
            name=self.name,
            state=state or self.state,
            volatility=self.volatility,
            oos_sharpe=self.oos_sharpe,
            horizon=self.horizon,
            max_drawdown_p95=self.max_drawdown_p95,
        )


class VetoInfo(_Frozen):
    """Veto de noticias vigente para el símbolo del evento."""

    scope: str
    impact: str
    reason: str
    expires_at: datetime

    @classmethod
    def from_veto(cls, veto: Veto) -> VetoInfo:
        return cls(
            scope=veto.scope,
            impact=str(veto.impact.value if hasattr(veto.impact, "value") else veto.impact),
            reason=veto.reason,
            expires_at=veto.expires_at,
        )


class SignalEvent(_Frozen):
    """Una señal ya evaluada por la capa compartida (ver el formato en el docstring)."""

    id: str
    strategy: str
    state: StrategyState
    symbol: str = Field(pattern=r"^[A-Z0-9]{5,20}$")
    bar_close: datetime
    price: Decimal = Field(gt=0)
    signal: Signal
    veto: VetoInfo | None = None
    sim: SimResult | None = None
    sim_error: str | None = None
    stop_loss: Decimal = Field(gt=0)
    take_profit: Decimal = Field(gt=0)
    hard_stop: bool = False
    catalog: tuple[StrategySnapshot, ...] = ()

    @field_validator("bar_close")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("bar_close debe tener zona horaria (UTC)")
        return v.astimezone(UTC)

    @property
    def is_shadow(self) -> bool:
        return self.state is StrategyState.SHADOW

    def snapshot(self, name: str | None = None) -> StrategySnapshot | None:
        name = name or self.strategy
        return next((s for s in self.catalog if s.name == name), None)

    def to_json(self) -> str:
        return self.model_dump_json()


def event_id(strategy: str, symbol: str, bar_close: datetime) -> str:
    ts = bar_close.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"{strategy}:{symbol}:{ts}"


def parse_event(fields: Mapping[Any, Any]) -> SignalEvent | None:
    """Entrada del stream -> evento; `None` si viene malformada."""
    raw = fields.get("data", fields.get(b"data"))
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        return SignalEvent.model_validate(json.loads(raw or "null"))
    except (ValidationError, json.JSONDecodeError, TypeError):
        return None


def catalog_from_records(records: Sequence[StrategyRecord]) -> tuple[StrategySnapshot, ...]:
    return tuple(StrategySnapshot.from_record(r) for r in records)


def _finite(value: Any, default: float | None) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out == out and abs(out) != float("inf") else default


__all__ = [
    "LIVE_STATES",
    "SignalEvent",
    "StrategySnapshot",
    "VetoInfo",
    "catalog_from_records",
    "event_id",
    "parse_event",
]
