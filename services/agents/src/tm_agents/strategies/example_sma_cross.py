"""ExampleSmaCrossAgent: ejemplo de la skill `nueva-estrategia`; no activar en producción.

**Este archivo se produjo siguiendo la skill `.claude/skills/nueva-estrategia/SKILL.md`**
como demostración de que el flujo da código que compila y pasa pruebas. **No tiene evidencia
publicada propia** y por eso se queda en `state=research` en el `StrategyRegistry`: no recibe
capital. Si quieres un sleeve basado en medias móviles, implementa el `MovingAverageAgent` de
la sección 3.2 de `docs/estrategias/investigacion-nuevas-estrategias.md`, que sí tiene
evidencia revisada por pares (Detzel et al., Financial Management 50(1), 2021).

Regla de ejemplo (cruce de medias simples, solo largo y sin look-ahead):
- Al cierre diario, con las velas ya cerradas en `ctx.now`:
  - `long` si la SMA(fast) está estrictamente por encima de la SMA(slow).
  - `flat` (USDT) en otro caso.
- La confianza es `min(1, |fast - slow| / slow)`: cuanto más separadas las medias, más
  convicción, acotada a 1 para no apalancar.

Solo expone métricas básicas en `metadata` (`bars_used`, `last_close`, `sma_fast`, `sma_slow`,
`spread`). Es intencionalmente simple: el valor del archivo está en demostrar el scaffolding,
no en la estrategia en sí.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from tm_shared.contracts import Direction, Horizon, Signal, Timeframe

from tm_agents.base import AgentContext, BaseAgent, register_strategy
from tm_agents.strategies.trend import closed_bars

DEFAULT_FAST = 20
DEFAULT_SLOW = 50
PERIOD = pd.Timedelta(days=1)


def sma_cross_signal(close: np.ndarray, fast: int, slow: int) -> tuple[int, float, float, float]:
    """Núcleo puro de la regla: `(direction_int, sma_fast, sma_slow, spread)`.

    - `direction_int`: +1 si fast > slow, 0 si no (en spot no operamos cortos).
    - `spread`: `(fast - slow) / slow`, en magnitud escala la confianza.
    """
    if slow <= fast:
        raise ValueError("slow debe ser mayor que fast")
    if len(close) < slow:
        return 0, float("nan"), float("nan"), 0.0
    f = float(close[-fast:].mean())
    s = float(close[-slow:].mean())
    spread = (f - s) / s if s != 0 else 0.0
    return (1 if f > s else 0), f, s, float(spread)


@register_strategy("example_sma_cross")
class ExampleSmaCrossAgent(BaseAgent):
    timeframe = Timeframe.D1

    def __init__(
        self,
        fast: int = DEFAULT_FAST,
        slow: int = DEFAULT_SLOW,
        **params: object,
    ) -> None:
        super().__init__(fast=fast, slow=slow, **params)
        if fast < 2:
            raise ValueError("fast debe ser >= 2 velas")
        if slow <= fast:
            raise ValueError("slow debe ser mayor que fast")
        self.fast = int(fast)
        self.slow = int(slow)

    def analyze(self, ctx: AgentContext) -> Signal:
        now = pd.Timestamp(ctx.now).tz_convert("UTC")
        bars = closed_bars(ctx.bars_for(self.timeframe), now, PERIOD)

        if len(bars) < self.slow:
            return Signal(
                symbol=ctx.symbol,
                direction=Direction.FLAT,
                confidence=0.0,
                horizon=Horizon.MEDIUM,
                timeframe=self.timeframe,
                source=self.name,
                generated_at=now.to_pydatetime(),
                metadata={
                    "bars_used": len(bars),
                    "last_close": float(bars["close"].iloc[-1]) if len(bars) else None,
                    "sma_fast": None,
                    "sma_slow": None,
                    "spread": 0.0,
                    "reason": "historia insuficiente",
                },
            )

        close = bars["close"].to_numpy(dtype=float)
        direction_int, sma_fast, sma_slow, spread = sma_cross_signal(close, self.fast, self.slow)
        direction = Direction.LONG if direction_int > 0 else Direction.FLAT
        confidence = float(min(1.0, abs(spread))) if direction_int > 0 else 0.0

        return Signal(
            symbol=ctx.symbol,
            direction=direction,
            confidence=confidence,
            horizon=Horizon.MEDIUM,
            timeframe=self.timeframe,
            source=self.name,
            generated_at=now.to_pydatetime(),
            metadata={
                "bars_used": len(bars),
                "last_close": float(close[-1]),
                "sma_fast": sma_fast,
                "sma_slow": sma_slow,
                "spread": spread,
            },
        )
