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

**Volatility targeting (#83, opcional con `vol_target`).** Cada periodo invierte
`min(1, vol_target / vol_realizada)` de su parte del sleeve, con la volatilidad anualizada
de los últimos `vol_window` retornos diarios de velas cerradas (sin look-ahead y sin
apalancamiento). Para no rebalancear por ruido, la exposición de un periodo solo cambia
cuando entra o sale, o cuando la escala nueva se aleja más de `rebalance_band` (10 puntos)
de la vigente. La banda se aplica sobre la historia de velas cerradas, así que el resultado
es determinista y el laboratorio lo reproduce igual.

Con `vol_target`, la `confidence` del `Signal` **es la exposición objetivo del sleeve**
(voto promedio por la escala, en [0, 1]) y `metadata` trae el detalle:

- `exposure`: lo mismo que `confidence` (fracción del sleeve que debe estar invertida).
- `vote`: el voto promedio sin escalar (la `confidence` que tendría sin vol targeting).
- `vol_scale`: `min(1, vol_target / vol_realizada)` en la última vela cerrada.
- `realized_vol`, `vol_target`, `vol_window`, `rebalance_band` y `exposures` por periodo.

Interfaz con el orquestador (#19): el ejecutor del sleeve compra o vende hasta que la
fracción invertida sea `metadata["exposure"]` (= `confidence`). Si la exposición vigente
del sleeve difiere de la nueva en menos que `rebalance_band`, no hace falta operar; el agente
ya aplica esa banda por periodo, así que basta con seguir `exposure`.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd
from tm_shared.contracts import Direction, Horizon, Signal, Timeframe

from tm_agents.base import AgentContext, BaseAgent, register_strategy

DEFAULT_LOOKBACKS: tuple[int, ...] = (20, 50, 100, 200)
DEFAULT_VOL_WINDOW = 30
DEFAULT_REBALANCE_BAND = 0.10
DAYS_PER_YEAR = 365


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


def realized_vol(close: np.ndarray, window: int) -> np.ndarray:
    """Volatilidad anualizada (365 días) por vela con los `window` log-retornos hasta `t`.

    En `t` solo usa cierres hasta `t` (la vela que acaba de cerrar): no hay look-ahead. Es
    `NaN` mientras no haya `window` retornos.
    """
    if window < 2:
        raise ValueError("vol_window debe ser de al menos 2 días")
    close = np.asarray(close, dtype=float)
    logret = pd.Series(np.log(close)).diff()
    vol = logret.rolling(window, min_periods=window).std(ddof=1) * np.sqrt(DAYS_PER_YEAR)
    return vol.to_numpy()


def vol_scale(vol: np.ndarray, vol_target: float) -> np.ndarray:
    """Escala `min(1, vol_target / vol)`: nunca apalanca. Sin dato (`NaN`) es 0 (no opera)."""
    vol = np.asarray(vol, dtype=float)
    out = np.zeros_like(vol)
    known = ~np.isnan(vol)
    positive = known & (vol > 0)
    out[known & ~positive] = 1.0  # precio constante: volatilidad cero, sin recorte
    out[positive] = np.minimum(1.0, vol_target / vol[positive])
    return out


def sized_exposure(
    state: np.ndarray, scale: np.ndarray, band: float = DEFAULT_REBALANCE_BAND
) -> np.ndarray:
    """Exposición por vela de un periodo (en [0, 1]) con la banda de rebalanceo.

    - Fuera de mercado (`state == 0`): 0.
    - Al entrar o cambiar de lado: la escala de esa vela.
    - Con la posición abierta: se conserva la exposición vigente salvo que la escala nueva se
      aleje más de `band`; entonces se rebalancea a la escala nueva.
    """
    if not 0 <= band < 1:
        raise ValueError("rebalance_band debe estar en [0, 1)")
    state = np.asarray(state)
    scale = np.asarray(scale, dtype=float)
    out = np.zeros(len(state))
    current, prev = 0.0, 0
    for t in range(len(state)):
        s = int(state[t])
        if s == 0:
            current = 0.0
        elif s != prev or abs(scale[t] - current) > band:
            current = float(scale[t])
        out[t] = current
        prev = s
    return out


@register_strategy("trend")
class TrendAgent(BaseAgent):
    timeframe = Timeframe.D1

    def __init__(
        self,
        lookbacks: Sequence[int] = DEFAULT_LOOKBACKS,
        allow_short: bool = False,
        vol_window: int = DEFAULT_VOL_WINDOW,
        vol_target: float | None = None,
        rebalance_band: float = DEFAULT_REBALANCE_BAND,
        **params: object,
    ) -> None:
        super().__init__(
            lookbacks=list(lookbacks),
            allow_short=allow_short,
            vol_window=vol_window,
            vol_target=vol_target,
            rebalance_band=rebalance_band,
            **params,
        )
        if not lookbacks or any(n < 2 for n in lookbacks):
            raise ValueError("lookbacks debe tener periodos >= 2")
        if vol_window < 2:
            raise ValueError("vol_window debe ser de al menos 2 días")
        if vol_target is not None and not vol_target > 0:
            raise ValueError("vol_target debe ser positivo (0.40 = 40 % anual)")
        if not 0 <= rebalance_band < 1:
            raise ValueError("rebalance_band debe estar en [0, 1)")
        self.lookbacks = sorted(set(int(n) for n in lookbacks))
        self.allow_short = allow_short
        self.vol_window = int(vol_window)
        self.vol_target = None if vol_target is None else float(vol_target)
        self.rebalance_band = float(rebalance_band)

    def states(self, bars: pd.DataFrame) -> dict[int, np.ndarray]:
        """Estado (+1, -1 o 0) por vela de cada periodo."""
        h, lo, c = (bars[col].to_numpy(dtype=float) for col in ("high", "low", "close"))
        return {n: donchian_states(h, lo, c, n, self.allow_short) for n in self.lookbacks}

    def votes(self, bars: pd.DataFrame) -> dict[int, int]:
        return {n: int(st[-1]) if len(st) else 0 for n, st in self.states(bars).items()}

    def exposures(self, bars: pd.DataFrame) -> tuple[dict[int, float], float, float | None]:
        """Exposición con signo por periodo, la escala y la vol realizada de la última vela.

        Sin `vol_target` la exposición de cada periodo es su voto (+1, -1 o 0).
        """
        states = self.states(bars)
        if self.vol_target is None or not len(bars):
            return {n: float(st[-1]) if len(st) else 0.0 for n, st in states.items()}, 1.0, None
        vol = realized_vol(bars["close"].to_numpy(dtype=float), self.vol_window)
        scale = vol_scale(vol, self.vol_target)
        out = {
            n: float(np.sign(st[-1]) * sized_exposure(st, scale, self.rebalance_band)[-1])
            for n, st in states.items()
        }
        last_vol = float(vol[-1]) if np.isfinite(vol[-1]) else None
        return out, float(scale[-1]), last_vol

    def analyze(self, ctx: AgentContext) -> Signal:
        now = pd.Timestamp(ctx.now).tz_convert("UTC")
        bars = closed_bars(ctx.bars_for(self.timeframe), now, pd.Timedelta(days=1))
        votes = self.votes(bars)
        score = float(np.mean(list(votes.values())))
        exposures, scale, last_vol = self.exposures(bars)
        exposure = abs(float(np.mean(list(exposures.values()))))
        if score > 0:
            direction = Direction.LONG
        elif score < 0:
            direction = Direction.SHORT
        else:
            direction = Direction.FLAT
        returns = np.log(bars["close"]).diff().dropna().tail(self.vol_window)
        ann_vol = float(returns.std(ddof=1) * np.sqrt(365)) if len(returns) > 1 else None
        metadata: dict[str, object] = {
            "votes": {str(n): v for n, v in votes.items()},
            "bars_used": len(bars),
            "annualized_vol": ann_vol,
            "last_close": float(bars["close"].iloc[-1]) if len(bars) else None,
        }
        if self.vol_target is not None:
            metadata.update(
                {
                    "exposure": exposure,
                    "vote": abs(score),
                    "vol_scale": scale,
                    "realized_vol": last_vol,
                    "vol_target": self.vol_target,
                    "vol_window": self.vol_window,
                    "rebalance_band": self.rebalance_band,
                    "exposures": {str(n): e for n, e in exposures.items()},
                }
            )
        return Signal(
            symbol=ctx.symbol,
            direction=direction,
            confidence=min(1.0, exposure),
            horizon=Horizon.MEDIUM,
            timeframe=self.timeframe,
            source=self.name,
            generated_at=now.to_pydatetime(),
            metadata=metadata,
        )
