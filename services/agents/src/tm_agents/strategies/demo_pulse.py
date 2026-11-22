"""DemoPulseAgent: estrategia **solo para demo local**, para ver el flujo completo en minutos.

**No es una estrategia de trading y nunca debe estar activa en dev ni en prod.** Las
estrategias reales operan con velas de 4 h y diarias, así que en una demo de media hora no
darían ninguna señal. Esta alterna ``long`` / ``flat`` en BTCUSDT cada
``TM_DEMO_PULSE_MINUTES`` minutos (2 por omisión) sobre velas de 1 minuto, y pasa por el mismo
camino que cualquier estrategia:

    capa compartida (señal + SimulationGate) → tm:signals → BotRunner (allocator, exposición,
    RiskAgent) → tm:decisions → engine (sim o Testnet) → /internal/trades → D1 → dashboard

Protecciones para que no se cuele a la nube:

- El módulo **no** se importa desde ``tm_agents.strategies``: el worker ``agents`` solo lo
  registra con ``TM_DEMO_STRATEGY=1`` (:func:`tm_agents.__main__.register_demo_strategy`), que
  solo pone ``make dev`` (``docker-compose.yml`` local). Sin el registro, una fila
  ``demo_pulse`` en ``strategies`` no tiene código y la capa compartida la ignora.
- La fila ``demo_pulse`` (en ``active``) solo está en ``apps/api/seed/seed.sql``, que se carga
  en la D1 local; ninguna migración la crea.

Para que la SimulationGate la deje pasar, :meth:`DemoPulseAgent.trajectories` entrega un
pronóstico **sintético y optimista** (sube ``drift`` en ``steps`` velas) que la gate combina con
el bootstrap histórico, igual que hace con las trayectorias de Kronos. Es parte del truco de
la demo: no dice nada del mercado.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
from tm_shared.contracts import Direction, Horizon, Signal, Timeframe

from tm_agents.base import AgentContext, BaseAgent, register_strategy

DEMO_NAME = "demo_pulse"
PULSE_ENV = "TM_DEMO_PULSE_MINUTES"
DEFAULT_PULSE_MINUTES = 2
#: Velas del pronóstico sintético (= ``gate_horizon`` de su política).
DEMO_STEPS = 5


def pulse_minutes(value: object | None = None) -> int:
    """Minutos de cada fase; ``TM_DEMO_PULSE_MINUTES`` si no se da (mínimo 1)."""
    raw = value if value is not None else os.environ.get(PULSE_ENV) or DEFAULT_PULSE_MINUTES
    minutes = int(raw)
    if minutes < 1:
        raise ValueError(f"{PULSE_ENV} debe ser al menos 1")
    return minutes


def pulse_is_long(now: pd.Timestamp, minutes: int) -> bool:
    """Fase del pulso en ``now``: ``long`` las primeras ``minutes`` de cada ciclo de 2 fases."""
    epoch_minutes = int(now.timestamp() // 60)
    return (epoch_minutes // minutes) % 2 == 0


@register_strategy(DEMO_NAME)
class DemoPulseAgent(BaseAgent):
    """Alterna long/flat cada ``minutes`` minutos en ``symbols`` (ver el módulo)."""

    timeframe = Timeframe.M1

    def __init__(
        self,
        minutes: int | None = None,
        symbols: list[str] | tuple[str, ...] = ("BTCUSDT",),
        confidence: float = 1.0,
        drift: float = 0.02,
        **params: object,
    ) -> None:
        super().__init__(minutes=minutes, symbols=symbols, confidence=confidence, **params)
        self.minutes = pulse_minutes(minutes)
        self.symbols = tuple(str(s).upper() for s in symbols)
        if not 0 < confidence <= 1:
            raise ValueError("confidence debe estar en (0, 1]")
        self.confidence = float(confidence)
        self.drift = float(drift)

    def analyze(self, ctx: AgentContext) -> Signal:
        now = pd.Timestamp(ctx.now)
        now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
        bars = ctx.bars_for(self.timeframe)
        long = ctx.symbol in self.symbols and len(bars) > 0 and pulse_is_long(now, self.minutes)
        return Signal(
            symbol=ctx.symbol,
            direction=Direction.LONG if long else Direction.FLAT,
            confidence=self.confidence if long else 0.0,
            horizon=Horizon.SHORT,
            timeframe=self.timeframe,
            source=self.name,
            generated_at=now.to_pydatetime(),
            metadata={
                "demo": True,
                "pulse_minutes": self.minutes,
                "phase": "long" if long else "flat",
                "last_close": float(bars["close"].iloc[-1]) if len(bars) else None,
            },
        )

    def trajectories(self, ctx: AgentContext, n: int = 200, steps: int = DEMO_STEPS):
        """Pronóstico sintético ``(n, steps, 4)`` en OHLC que sube ``drift`` (solo demo)."""
        bars = ctx.bars_for(self.timeframe)
        last = float(bars["close"].iloc[-1])
        rng = np.random.default_rng(42)
        trend = last * (1 + self.drift * np.arange(1, steps + 1) / steps)
        close = trend * (1 + rng.normal(0.0, 0.0005, size=(n, steps)))
        opens = np.concatenate([np.full((n, 1), last), close[:, :-1]], axis=1)
        high = np.maximum(opens, close) * 1.0002
        low = np.minimum(opens, close) * 0.9998
        return np.stack([opens, high, low, close], axis=2)


__all__ = ["DEMO_NAME", "DemoPulseAgent", "pulse_is_long", "pulse_minutes"]
