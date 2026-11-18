"""TsmomAgent: time-series momentum con rebalanceo semanal (velas diarias).

Evidencia: Liu, Y. y Tsyvinski, A., *Risks and Returns of Cryptocurrency*, Review of
Financial Studies 34(6), 2021 (DOI 10.1093/rfs/hhaa113), tablas 14-17 (NBER WP 24877). El
retorno semanal de BTC predice el de 1-4 semanas siguientes con cortes fijados sin
look-ahead. En ETH la evidencia es más débil. La muestra del paper termina en 2018, así
que 2020-2025 es out-of-sample.

Regla (solo spot, sin cortos; `long` o `flat`):
- `anchor="sunday"`: una vez por semana (los domingos UTC) se mira r = close / close[-L] - 1.
  Si r > 0 → `long` los 7 días siguientes; si no → `flat`. La señal del día actual depende
  del último anclaje aplicable (el domingo más reciente con historia suficiente).
- `anchor="staggered"` (por defecto, robusto al día de anclaje): el sleeve se divide en 7
  tramos diarios; cada tramo aplica la regla en su día de la semana y la mantiene 7 días.
  La confianza es la fracción de tramos actualmente en largo (0, 1/7, …, 1).

El parámetro `lookback` admite 7, 14 o 28 días (el paper usa 7). Se eligen `lookback` y
`anchor` en el laboratorio (#13), que hace walk-forward y Monte Carlo antes de promover.

`metadata` lleva el `r` del último anclaje aplicable, el número de tramos en largo
(`tranches_long`, de 0 a 7 para staggered, 0 o 1 para sunday) y la volatilidad anualizada
(desviación estándar de log-retornos diarios por sqrt(365) sobre las últimas 60 velas
cerradas).

Sin look-ahead: `closed_bars` filtra las velas cuyo cierre ya ocurrió en `ctx.now`, así que
añadir velas futuras después de `ctx.now` no cambia la señal. Determinista.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from tm_shared.contracts import Direction, Horizon, Signal, Timeframe

from tm_agents.base import AgentContext, BaseAgent, register_strategy
from tm_agents.strategies.trend import closed_bars

ALLOWED_LOOKBACKS: tuple[int, ...] = (7, 14, 28)
ALLOWED_ANCHORS: tuple[str, ...] = ("sunday", "staggered")
HOLD_DAYS = 7
VOL_WINDOW = 60
DAYS_PER_YEAR = 365
SUNDAY = 6  # pandas: lunes=0, …, domingo=6


def _annualized_vol(close: np.ndarray, window: int = VOL_WINDOW) -> float | None:
    """Desviación estándar de log-retornos por sqrt(365) sobre las últimas `window` velas."""
    close = np.asarray(close, dtype=float)
    if len(close) < 2:
        return None
    logret = np.diff(np.log(close))
    tail = logret[-window:]
    if len(tail) < 2:
        return None
    sigma = float(np.std(tail, ddof=1))
    return sigma * np.sqrt(DAYS_PER_YEAR) if np.isfinite(sigma) else None


def _last_sunday_index(dates: pd.DatetimeIndex, min_pos: int) -> int | None:
    """Última posición >= `min_pos` cuya fecha (UTC) cae en domingo; None si no hay."""
    weekdays = dates.day_of_week
    for i in range(len(dates) - 1, min_pos - 1, -1):
        if weekdays[i] == SUNDAY:
            return i
    return None


def _tranche_long(
    close: np.ndarray, dates: pd.DatetimeIndex, dow: int, lookback: int, now_pos: int
) -> bool:
    """¿El tramo anclado al día `dow` está largo en la vela `now_pos`?

    Recorre hacia atrás hasta `HOLD_DAYS` días buscando el anclaje más reciente del tramo
    (vela cuyo día de la semana sea `dow` con historia >= `lookback`). Si lo encuentra,
    devuelve si r = close[anchor] / close[anchor - lookback] - 1 > 0. Si no, False.
    """
    start = max(now_pos - HOLD_DAYS + 1, lookback)
    for i in range(now_pos, start - 1, -1):
        if dates.day_of_week[i] == dow:
            r = close[i] / close[i - lookback] - 1.0
            return bool(np.isfinite(r) and r > 0)
    return False


@register_strategy("tsmom")
class TsmomAgent(BaseAgent):
    timeframe = Timeframe.D1

    def __init__(
        self,
        lookback: int = 7,
        anchor: str = "staggered",
        **params: object,
    ) -> None:
        super().__init__(lookback=lookback, anchor=anchor, **params)
        if int(lookback) not in ALLOWED_LOOKBACKS:
            raise ValueError(f"lookback debe ser uno de {ALLOWED_LOOKBACKS}")
        if anchor not in ALLOWED_ANCHORS:
            raise ValueError(f"anchor debe ser uno de {ALLOWED_ANCHORS}")
        self.lookback = int(lookback)
        self.anchor = anchor

    def analyze(self, ctx: AgentContext) -> Signal:
        now = pd.Timestamp(ctx.now).tz_convert("UTC")
        bars = closed_bars(ctx.bars_for(self.timeframe), now, pd.Timedelta(days=1))
        min_bars = self.lookback + HOLD_DAYS
        close = bars["close"].to_numpy(dtype=float) if len(bars) else np.empty(0)
        ann_vol = _annualized_vol(close) if len(close) >= 2 else None

        if len(bars) < min_bars:
            return Signal(
                symbol=ctx.symbol,
                direction=Direction.FLAT,
                confidence=0.0,
                horizon=Horizon.MEDIUM,
                timeframe=self.timeframe,
                source=self.name,
                generated_at=now.to_pydatetime(),
                metadata={
                    "r": None,
                    "tranches_long": 0,
                    "annualized_vol": ann_vol,
                    "anchor": self.anchor,
                    "lookback": self.lookback,
                    "bars_used": len(bars),
                    "reason": (
                        f"historial insuficiente: {len(bars)} < {min_bars} velas cerradas"
                    ),
                },
            )

        dates = pd.DatetimeIndex(bars.index)
        dates = dates.tz_localize("UTC") if dates.tz is None else dates.tz_convert("UTC")
        now_pos = len(close) - 1

        if self.anchor == "sunday":
            tranches_long, r_value = self._sunday_vote(close, dates, now_pos)
            total = 1
        else:
            tranches_long, r_value = self._staggered_vote(close, dates, now_pos)
            total = HOLD_DAYS

        confidence = tranches_long / total
        direction = Direction.LONG if tranches_long > 0 else Direction.FLAT
        return Signal(
            symbol=ctx.symbol,
            direction=direction,
            confidence=confidence,
            horizon=Horizon.MEDIUM,
            timeframe=self.timeframe,
            source=self.name,
            generated_at=now.to_pydatetime(),
            metadata={
                "r": r_value,
                "tranches_long": int(tranches_long),
                "annualized_vol": ann_vol,
                "anchor": self.anchor,
                "lookback": self.lookback,
                "bars_used": len(bars),
                "last_close": float(close[-1]),
            },
        )

    def _sunday_vote(
        self, close: np.ndarray, dates: pd.DatetimeIndex, now_pos: int
    ) -> tuple[int, float | None]:
        """Para `anchor="sunday"`: devuelve (1, r) si el último anclaje dominical es largo."""
        start = max(now_pos - HOLD_DAYS + 1, self.lookback)
        anchor_pos = _last_sunday_index(dates[: now_pos + 1], start)
        if anchor_pos is None:
            return 0, None
        r = float(close[anchor_pos] / close[anchor_pos - self.lookback] - 1.0)
        if not np.isfinite(r):
            return 0, None
        return (1 if r > 0 else 0), r

    def _staggered_vote(
        self, close: np.ndarray, dates: pd.DatetimeIndex, now_pos: int
    ) -> tuple[int, float | None]:
        """Para `anchor="staggered"`: suma los 7 tramos en largo; r = del tramo de hoy."""
        tranches_long = 0
        r_today: float | None = None
        today_dow = int(dates.day_of_week[now_pos])
        for dow in range(7):
            if _tranche_long(close, dates, dow, self.lookback, now_pos):
                tranches_long += 1
            if dow == today_dow:
                r_today = float(close[now_pos] / close[now_pos - self.lookback] - 1.0)
        if r_today is not None and not np.isfinite(r_today):
            r_today = None
        return tranches_long, r_today
