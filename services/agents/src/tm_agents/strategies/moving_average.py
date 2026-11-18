"""MovingAverageAgent: filtro de tendencia con medias móviles simples (velas diarias).

Evidencia: Detzel, Liu, Strauss, Zhou y Zhu, *Learning and predictability via technical
analysis: Evidence from bitcoin and stocks with (almost) no fundamentals*, Financial
Management 50 (1), 2021 (DOI 10.1111/fima.12310). Para Bitcoin documentan que reglas
sencillas de medias móviles (sobre todo con ventanas largas, como 50 y 100 días) baten
al buy-and-hold ajustando por riesgo, en un activo sin flujos de caja donde los
inversionistas aprenden de los propios precios.

Regla por cada ventana N (sin look-ahead, solo con velas cerradas):
- Largo si el último cierre cerrado es estrictamente mayor que la SMA de los N cierres
  cerrados **previos** (no se incluye la vela actual).
- `flat` en otro caso: la estrategia es solo-largo (Binance Spot) y nunca abre cortos.
- Si no hay al menos `max(windows) + 1` velas cerradas, la señal es `flat` por falta de
  historia (la SMA de la ventana más larga requiere `max(windows)` cierres previos al
  del último).

La señal del agente es `long` si **alguna** ventana está en largo y `flat` si ninguna lo
está; la `confidence` es la fracción de ventanas en largo (0, 0.5, 1 con 50/100). En
`metadata` se guarda el detalle para el orquestador y el laboratorio:

- `sma_<N>`: valor de la media móvil de la ventana N en la última vela cerrada.
- `ratio_<N>`: cociente `close/sma_<N>` (>1 implica largo en esa ventana).
- `last_close`, `bars_used`, `windows`: contexto numérico de la decisión.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd
from tm_shared.contracts import Direction, Horizon, Signal, Timeframe

from tm_agents.base import AgentContext, BaseAgent, register_strategy
from tm_agents.strategies.trend import closed_bars

DEFAULT_WINDOWS: tuple[int, ...] = (50, 100)


@register_strategy("moving_average")
class MovingAverageAgent(BaseAgent):
    """Filtro de tendencia por SMA (Detzel et al. 2021), solo-largo, timeframe diario."""

    timeframe = Timeframe.D1

    def __init__(
        self,
        windows: Sequence[int] = DEFAULT_WINDOWS,
        **params: object,
    ) -> None:
        super().__init__(windows=list(windows), **params)
        if not windows or any(int(n) < 2 for n in windows):
            raise ValueError("windows debe tener ventanas >= 2")
        self.windows = sorted({int(n) for n in windows})

    def analyze(self, ctx: AgentContext) -> Signal:
        now = pd.Timestamp(ctx.now).tz_convert("UTC")
        bars = closed_bars(ctx.bars_for(self.timeframe), now, pd.Timedelta(days=1))
        closes = bars["close"].to_numpy(dtype=float)
        max_window = max(self.windows)

        metadata: dict[str, object] = {
            "windows": list(self.windows),
            "bars_used": len(bars),
            "last_close": float(closes[-1]) if len(closes) else None,
        }

        if len(closes) < max_window + 1:
            # Sin historia suficiente para la SMA más larga; nos quedamos en flat.
            for n in self.windows:
                metadata[f"sma_{n}"] = None
                metadata[f"ratio_{n}"] = None
            return Signal(
                symbol=ctx.symbol,
                direction=Direction.FLAT,
                confidence=0.0,
                horizon=Horizon.MEDIUM,
                timeframe=self.timeframe,
                source=self.name,
                generated_at=now.to_pydatetime(),
                metadata=metadata,
            )

        last_close = float(closes[-1])
        long_votes = 0
        for n in self.windows:
            # SMA de los N cierres previos al último cierre cerrado: sin look-ahead.
            prior = closes[-(n + 1) : -1]
            sma = float(np.mean(prior))
            ratio = last_close / sma if sma > 0 else float("nan")
            metadata[f"sma_{n}"] = sma
            metadata[f"ratio_{n}"] = float(ratio) if np.isfinite(ratio) else None
            if last_close > sma:
                long_votes += 1

        confidence = long_votes / len(self.windows)
        direction = Direction.LONG if long_votes > 0 else Direction.FLAT
        return Signal(
            symbol=ctx.symbol,
            direction=direction,
            confidence=confidence,
            horizon=Horizon.MEDIUM,
            timeframe=self.timeframe,
            source=self.name,
            generated_at=now.to_pydatetime(),
            metadata=metadata,
        )
