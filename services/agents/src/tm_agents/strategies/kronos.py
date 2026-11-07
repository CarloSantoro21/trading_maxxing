"""KronosAgent: señal y trayectorias de Kronos sobre velas de 4 horas.

Evidencia: Shi et al., *Kronos: A Foundation Model for the Language of Financial Markets*
(AAAI 2026). Kronos es generativo: en cada cierre de 4 h se piden `n_trajectories`
trayectorias (default 20) al servicio forecast (`POST /predict`) y se resumen así:

- Retorno de cada trayectoria: `close` de su última vela / último cierre real - 1.
- Dirección: por la **mediana** de esos retornos. En spot solo hay `long` (mediana mayor
  que `min_return`) o `flat`; con `allow_short=True` una mediana menor que `-min_return`
  da `short` (desactivado: Binance Spot).
- Confianza: fracción de trayectorias cuyo retorno coincide con la dirección (suben si es
  `long`, bajan si es `short`, no suben si es `flat`).
- `metadata` trae además las probabilidades de tocar primero el TP o el SL (con los
  porcentajes configurados) y estadísticas de los retornos.

`trajectories(ctx)` devuelve el arreglo de precios `(n, pred_len, 5)` en orden OHLCV para la
SimulationGate. El pronóstico de un mismo `(symbol, now)` se pide una sola vez y se reutiliza.

Si forecast no responde (timeout, error HTTP o respuesta inválida), la señal es `flat` con
confianza 0 y el error va en `metadata["error"]`.

Las velas de 4 h salen del contexto si las trae (`Timeframe.H4`); si no, se remuestrean de
15m, 30m o 1m con `tm_marketdata.resample_ohlcv` (solo cubos completos, sin look-ahead).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx
import numpy as np
import pandas as pd
from tm_marketdata.resample import resample_ohlcv
from tm_shared.contracts import Direction, Horizon, Signal, Timeframe

from tm_agents.base import AgentContext, BaseAgent, register_strategy
from tm_agents.strategies.trend import closed_bars

OHLCV = ("open", "high", "low", "close", "volume")
H4 = pd.Timedelta(hours=4)
# Timeframes de origen para remuestrear a 4 h, en orden de preferencia, con su duración
SOURCE_TIMEFRAMES: tuple[tuple[Timeframe, pd.Timedelta], ...] = (
    (Timeframe.M15, pd.Timedelta(minutes=15)),
    (Timeframe.M30, pd.Timedelta(minutes=30)),
    (Timeframe.M1, pd.Timedelta(minutes=1)),
)
MIN_CANDLES = 32  # mínimo que acepta el servicio forecast
MAX_CONTEXT = 512  # contexto máximo de Kronos-small
DEFAULT_FORECAST_URL = "http://localhost:8001"
DEFAULT_TIMEOUT = 120.0


class ForecastError(RuntimeError):
    """El servicio forecast no respondió o devolvió algo inválido."""


@dataclass(frozen=True)
class ForecastPaths:
    """Trayectorias de un pronóstico: `paths` con forma `(n, pred_len, 5)` en orden OHLCV."""

    timestamps: pd.DatetimeIndex
    paths: np.ndarray
    model: str | None = None
    device: str | None = None


class ForecastClient:
    """Cliente HTTP síncrono de `POST /predict` del servicio forecast."""

    def __init__(
        self,
        base_url: str = DEFAULT_FORECAST_URL,
        timeout: float = DEFAULT_TIMEOUT,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._transport = transport

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> ForecastClient:
        """`FORECAST_URL` (default `http://localhost:8001`) y `FORECAST_TIMEOUT` en segundos."""
        env = os.environ if env is None else env
        return cls(
            base_url=env.get("FORECAST_URL") or DEFAULT_FORECAST_URL,
            timeout=float(env.get("FORECAST_TIMEOUT") or DEFAULT_TIMEOUT),
        )

    def predict(
        self,
        candles: pd.DataFrame,
        *,
        pred_len: int,
        samples: int,
        seed: int | None = None,
        temperature: float = 1.0,
        top_p: float = 0.9,
    ) -> ForecastPaths:
        """Pide `samples` trayectorias de `pred_len` velas a partir de `candles` (índice UTC)."""
        body = {
            "candles": candles_payload(candles),
            "pred_len": pred_len,
            "samples": samples,
            "seed": seed,
            "temperature": temperature,
            "top_p": top_p,
        }
        try:
            with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
                resp = client.post(f"{self.base_url}/predict", json=body)
        except httpx.TimeoutException as exc:
            raise ForecastError(f"timeout de forecast ({self.timeout:g} s): {exc}") from exc
        except httpx.HTTPError as exc:
            raise ForecastError(f"forecast no responde: {exc}") from exc
        if resp.status_code != 200:
            raise ForecastError(f"forecast respondió {resp.status_code}: {resp.text[:200]}")
        try:
            return parse_predict_response(resp.json(), samples=samples, pred_len=pred_len)
        except (ValueError, KeyError, TypeError) as exc:
            raise ForecastError(f"respuesta de forecast inválida: {exc}") from exc


def candles_payload(bars: pd.DataFrame) -> list[dict[str, Any]]:
    """Convierte velas indexadas por `open_time` al formato `Candle` del servicio forecast."""
    idx = pd.DatetimeIndex(bars.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    values = bars[list(OHLCV)].to_numpy(dtype=float)
    return [
        {"timestamp": ts.isoformat(), **dict(zip(OHLCV, row.tolist(), strict=True))}
        for ts, row in zip(idx, values, strict=True)
    ]


def parse_predict_response(data: dict[str, Any], *, samples: int, pred_len: int) -> ForecastPaths:
    """Valida la respuesta de `/predict` y la convierte en un arreglo `(n, pred_len, 5)`."""
    trajectories = data["trajectories"]
    if len(trajectories) != samples:
        raise ValueError(f"se pidieron {samples} trayectorias y llegaron {len(trajectories)}")
    paths = np.array(
        [[[float(c[k]) for k in OHLCV] for c in path] for path in trajectories], dtype=float
    )
    if paths.shape != (samples, pred_len, len(OHLCV)):
        raise ValueError(f"forma inesperada de trayectorias: {paths.shape}")
    if not np.isfinite(paths).all():
        raise ValueError("hay valores no finitos en las trayectorias")
    stamps = pd.DatetimeIndex(pd.to_datetime(data.get("timestamps", []), utc=True))
    return ForecastPaths(
        timestamps=stamps, paths=paths, model=data.get("model"), device=data.get("device")
    )


def hit_probabilities(
    paths: np.ndarray, stop_loss: float, take_profit: float
) -> tuple[float, float, float]:
    """`(p_tp, p_sl, p_open)` de una compra: qué toca primero cada trayectoria.

    Se recorren las velas en orden; TP se toca si `high >= take_profit` y SL si
    `low <= stop_loss`. Si ambos caen en la misma vela se cuenta el SL (conservador).
    """
    if not stop_loss < take_profit:
        raise ValueError("el stop_loss debe quedar debajo del take_profit")
    n, steps = paths.shape[:2]
    tp_hit = paths[:, :, 1] >= take_profit
    sl_hit = paths[:, :, 2] <= stop_loss
    never = steps  # índice "sin toque"
    first_tp = np.where(tp_hit.any(axis=1), tp_hit.argmax(axis=1), never)
    first_sl = np.where(sl_hit.any(axis=1), sl_hit.argmax(axis=1), never)
    tp = (first_tp < first_sl).sum()
    sl = ((first_sl <= first_tp) & (first_sl < never)).sum()
    return tp / n, sl / n, (n - tp - sl) / n


def bars_4h(ctx: AgentContext) -> pd.DataFrame:
    """Velas de 4 h cerradas en `ctx.now`: las del contexto o remuestreadas de uno menor."""
    now = pd.Timestamp(ctx.now).tz_convert("UTC")
    if Timeframe.H4 in ctx.bars:
        return closed_bars(ctx.bars[Timeframe.H4], now, H4)[list(OHLCV)]
    for tf, period in SOURCE_TIMEFRAMES:
        if tf in ctx.bars:
            src = closed_bars(ctx.bars[tf], now, period)
            return resample_ohlcv(src[list(OHLCV)], "4h", period, now=now)
    raise KeyError(f"el contexto no trae velas de 4h, 15m, 30m ni 1m para {ctx.symbol}")


@register_strategy("kronos")
class KronosAgent(BaseAgent):
    timeframe = Timeframe.H4

    def __init__(
        self,
        n_trajectories: int = 20,
        pred_len: int = 6,
        context: int = 400,
        seed: int | None = 42,
        temperature: float = 1.0,
        top_p: float = 0.9,
        min_return: float = 0.0,
        allow_short: bool = False,
        stop_loss_pct: float = 0.02,
        take_profit_pct: float = 0.04,
        client: ForecastClient | None = None,
        **params: object,
    ) -> None:
        super().__init__(
            n_trajectories=n_trajectories,
            pred_len=pred_len,
            context=context,
            seed=seed,
            min_return=min_return,
            allow_short=allow_short,
            **params,
        )
        if n_trajectories < 1:
            raise ValueError("n_trajectories debe ser al menos 1")
        if pred_len < 1:
            raise ValueError("pred_len debe ser al menos 1")
        if not MIN_CANDLES <= context <= MAX_CONTEXT:
            raise ValueError(f"context debe estar entre {MIN_CANDLES} y {MAX_CONTEXT}")
        if not 0 < stop_loss_pct < 1 or take_profit_pct <= 0:
            raise ValueError("stop_loss_pct debe estar en (0, 1) y take_profit_pct ser > 0")
        self.n_trajectories = int(n_trajectories)
        self.pred_len = int(pred_len)
        self.context = int(context)
        self.seed = seed
        self.temperature = temperature
        self.top_p = top_p
        self.min_return = float(min_return)
        self.allow_short = allow_short
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.client = client or ForecastClient.from_env()
        self._cache: tuple[tuple[str, pd.Timestamp], pd.DataFrame, ForecastPaths] | None = None

    def _forecast(self, ctx: AgentContext) -> tuple[pd.DataFrame, ForecastPaths]:
        key = (ctx.symbol, pd.Timestamp(ctx.now).tz_convert("UTC"))
        if self._cache is not None and self._cache[0] == key:
            return self._cache[1], self._cache[2]
        bars = bars_4h(ctx).tail(self.context)
        if len(bars) < MIN_CANDLES:
            raise ForecastError(
                f"velas de 4h insuficientes: hay {len(bars)}, se necesitan {MIN_CANDLES}"
            )
        fc = self.client.predict(
            bars,
            pred_len=self.pred_len,
            samples=self.n_trajectories,
            seed=self.seed,
            temperature=self.temperature,
            top_p=self.top_p,
        )
        self._cache = (key, bars, fc)
        return bars, fc

    def trajectories(self, ctx: AgentContext) -> np.ndarray:
        """Precios pronosticados `(n_trajectories, pred_len, 5)` en orden OHLCV.

        Lanza `ForecastError` si forecast no responde (la SimulationGate decide qué hacer).
        """
        return self._forecast(ctx)[1].paths

    def analyze(self, ctx: AgentContext) -> Signal:
        now = pd.Timestamp(ctx.now).tz_convert("UTC")
        try:
            bars, fc = self._forecast(ctx)
        except ForecastError as exc:
            return self._signal(ctx, now, Direction.FLAT, 0.0, {"error": str(exc)})

        last_close = float(bars["close"].iloc[-1])
        returns = fc.paths[:, -1, 3] / last_close - 1.0
        median = float(np.median(returns))
        if median > self.min_return:
            direction, agree = Direction.LONG, returns > 0
        elif self.allow_short and median < -self.min_return:
            direction, agree = Direction.SHORT, returns < 0
        else:
            direction, agree = Direction.FLAT, returns <= 0
        sl = last_close * (1 - self.stop_loss_pct)
        tp = last_close * (1 + self.take_profit_pct)
        p_tp, p_sl, p_open = hit_probabilities(fc.paths, sl, tp)
        metadata = {
            "n_trajectories": int(fc.paths.shape[0]),
            "pred_len": int(fc.paths.shape[1]),
            "bars_used": len(bars),
            "last_close": last_close,
            "median_return": median,
            "mean_return": float(np.mean(returns)),
            "p_up": float(np.mean(returns > 0)),
            "return_p05": float(np.quantile(returns, 0.05)),
            "return_p95": float(np.quantile(returns, 0.95)),
            "stop_loss": sl,
            "take_profit": tp,
            "p_take_profit": p_tp,
            "p_stop_loss": p_sl,
            "p_open": p_open,
            "forecast_end": fc.timestamps[-1].isoformat() if len(fc.timestamps) else None,
            "model": fc.model,
            "device": fc.device,
        }
        return self._signal(ctx, now, direction, float(np.mean(agree)), metadata)

    def _signal(
        self,
        ctx: AgentContext,
        now: pd.Timestamp,
        direction: Direction,
        confidence: float,
        metadata: dict[str, Any],
    ) -> Signal:
        return Signal(
            symbol=ctx.symbol,
            direction=direction,
            confidence=confidence,
            horizon=Horizon.SHORT,
            timeframe=self.timeframe,
            source=self.name,
            generated_at=now.to_pydatetime(),
            metadata=metadata,
        )
