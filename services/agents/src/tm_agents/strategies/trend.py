"""TrendAgent: trend following con un conjunto de canales Donchian (velas diarias).

Evidencia: Liu y Tsyvinski (RFS 2021) documentan momentum de serie de tiempo
en BTC, ETH y XRP a escala diaria y semanal; Zarattini, Pagani y Barbon, *Catching Crypto
Trends* (SFI 25-80, 2025), usan un conjunto de modelos Donchian con varios periodos.

Regla por cada periodo N (con estado, sin look-ahead):
- Entra en largo cuando el cierre supera el máximo de los N días previos (sin contar hoy).
- Sale cuando el cierre cae por debajo del punto medio del canal de los N días previos
  (trailing stop). Con `allow_short` es simétrico para cortos (desactivado: Binance Spot).

La señal es el voto promedio del conjunto: confianza = fracción de periodos en largo
(o en corto). La volatilidad anualizada va en `metadata` para el dimensionamiento.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd
from tm_shared.contracts import Direction, Horizon, Signal, Timeframe

from tm_agents.base import AgentContext, BaseAgent, register_strategy

DEFAULT_LOOKBACKS: tuple[int, ...] = (20, 50, 100, 200)


def closed_bars(bars: pd.DataFrame, now: pd.Timestamp, period: pd.Timedelta) -> pd.DataFrame:
    """Solo velas cuyo cierre (`open_time + period`) ya ocurrió en `now`."""
    idx = pd.DatetimeIndex(bars.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    mask = (idx + period) <= now
    return bars.loc[mask]


def donchian_states(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int, allow_short: bool
) -> np.ndarray:
    """Estado por vela para el periodo `n`: +1 largo, -1 corto, 0 fuera."""
    size = len(close)
    state = np.zeros(size, dtype=np.int8)
    pos = 0
    for t in range(n, size):
        hh = high[t - n : t].max()
        ll = low[t - n : t].min()
        mid = (hh + ll) / 2.0
        c = close[t]
        if (pos == 1 and c < mid) or (pos == -1 and c > mid):
            pos = 0  # trailing stop en el punto medio del canal
        if pos == 0:
            if c > hh:
                pos = 1
            elif allow_short and c < ll:
                pos = -1
        state[t] = pos
    return state


@register_strategy("trend")
class TrendAgent(BaseAgent):
    timeframe = Timeframe.D1

    def __init__(
        self,
        lookbacks: Sequence[int] = DEFAULT_LOOKBACKS,
        allow_short: bool = False,
        vol_window: int = 30,
        **params: object,
    ) -> None:
        super().__init__(lookbacks=list(lookbacks), allow_short=allow_short, **params)
        if not lookbacks or any(n < 2 for n in lookbacks):
            raise ValueError("lookbacks debe tener periodos >= 2")
        self.lookbacks = sorted(set(int(n) for n in lookbacks))
        self.allow_short = allow_short
        self.vol_window = vol_window

    def votes(self, bars: pd.DataFrame) -> dict[int, int]:
        h, lo, c = (bars[col].to_numpy(dtype=float) for col in ("high", "low", "close"))
        return {
            n: int(donchian_states(h, lo, c, n, self.allow_short)[-1]) if len(c) > n else 0
            for n in self.lookbacks
        }

    def analyze(self, ctx: AgentContext) -> Signal:
        now = pd.Timestamp(ctx.now).tz_convert("UTC")
        bars = closed_bars(ctx.bars_for(self.timeframe), now, pd.Timedelta(days=1))
        votes = self.votes(bars)
        score = float(np.mean(list(votes.values())))
        if score > 0:
            direction = Direction.LONG
        elif score < 0:
            direction = Direction.SHORT
        else:
            direction = Direction.FLAT
        returns = np.log(bars["close"]).diff().dropna().tail(self.vol_window)
        ann_vol = float(returns.std(ddof=1) * np.sqrt(365)) if len(returns) > 1 else None
        return Signal(
            symbol=ctx.symbol,
            direction=direction,
            confidence=abs(score),
            horizon=Horizon.MEDIUM,
            timeframe=self.timeframe,
            source=self.name,
            generated_at=now.to_pydatetime(),
            metadata={
                "votes": {str(n): v for n, v in votes.items()},
                "bars_used": len(bars),
                "annualized_vol": ann_vol,
                "last_close": float(bars["close"].iloc[-1]) if len(bars) else None,
            },
        )
