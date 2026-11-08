"""Adaptadores de las estrategias de `tm_agents` al laboratorio.

Cada adaptador convierte la lógica de la estrategia en series objetivo por vela (1 = largo,
0 = USDT) sobre toda la historia disponible, con las mismas funciones que usa el agente en
`analyze(ctx)`. Calcularlas de una vez es equivalente a llamar `analyze` en cada cierre
(las funciones son causales: el valor en `t` solo depende de velas cerradas hasta `t`) y las
pruebas lo comprueban contra el agente. En spot, `long` = comprar o mantener y `flat` =
vender y quedarse en USDT; los cortos del paper o de `allow_short` no se operan.

- **trend** (`TrendAgent`, velas diarias): un sub-sleeve por periodo Donchian, cada uno con la
  misma fracción del capital. La exposición total es el voto promedio del conjunto (la
  `confidence` de la señal). La cuadrícula escala los cuatro periodos por un multiplicador.
- **trend_vol** (`TrendAgent(vol_target=...)`, #83): lo mismo con volatility targeting. Cada
  periodo invierte `min(1, vol_target / vol_realizada)` de su parte con la banda de
  rebalanceo del agente (`sized_exposure`). Como el sleeve spot es todo o nada, la parte de
  cada periodo se divide en `lots` lotes iguales (10 por defecto, 10 % cada uno) y se tienen
  comprados `round(exposición * lots)`; así un rebalanceo solo compra o vende los lotes que
  cambian (la comisión es la del cambio, no la de toda la posición). La cuadrícula agrega
  `vol_target` y `vol_window`.
- **intraday_momentum** (`IntradayMomentumAgent`, velas de 30 min): largo durante la última
  media hora (de 16:30 a 17:00 ET) cuando la regla del paper da largo. La apertura del reloj de
  volumen se ajusta con las velas in-sample de cada ventana.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

import numpy as np
import pandas as pd
from tm_agents.strategies.intraday_momentum import (
    DEFAULT_OPEN_WINDOW,
    MARKET_TZ,
    PENULTIMATE_SLOT,
    rule_is_long,
    session_table,
    slot_label,
    volume_clock_open,
)
from tm_agents.strategies.trend import (
    DEFAULT_LOOKBACKS,
    DEFAULT_REBALANCE_BAND,
    DEFAULT_VOL_WINDOW,
    donchian_states,
    realized_vol,
    sized_exposure,
    vol_scale,
)
from tm_shared.contracts import Timeframe

Params = dict[str, Any]


class StrategyAdapter(Protocol):
    name: str
    timeframe: Timeframe
    period: pd.Timedelta
    n_sub_sleeves: int
    lots: int

    def default_grid(self) -> list[Params]: ...

    def fit(self, params: Mapping[str, Any], is_bars: pd.DataFrame) -> Params: ...

    def targets(self, bars: pd.DataFrame, fitted: Mapping[str, Any]) -> list[pd.Series]: ...

    def describe(self, fitted: Mapping[str, Any]) -> str: ...


@dataclass
class TrendAdapter:
    """Conjunto Donchian del TrendAgent: un sub-sleeve por periodo (y por lote, con `lots`).

    Con `vol_targets` distinto de `(None,)` cada combinación de la cuadrícula lleva además
    `vol_target` y `vol_window`, y la exposición de cada periodo se reparte en `lots` lotes.
    """

    name: ClassVar[str] = "trend"
    timeframe: ClassVar[Timeframe] = Timeframe.D1
    period: ClassVar[pd.Timedelta] = pd.Timedelta(days=1)
    base_lookbacks: tuple[int, ...] = DEFAULT_LOOKBACKS
    multipliers: tuple[float, ...] = (0.5, 0.75, 1.0, 1.25)
    vol_targets: tuple[float | None, ...] = (None,)
    vol_windows: tuple[int, ...] = (DEFAULT_VOL_WINDOW,)
    rebalance_band: float = DEFAULT_REBALANCE_BAND
    lots: int = 1

    def __post_init__(self) -> None:
        if self.lots < 1:
            raise ValueError("lots debe ser al menos 1")
        if self.lots == 1 and any(v is not None for v in self.vol_targets):
            raise ValueError("el volatility targeting necesita lots > 1 (exposición fraccional)")

    @property
    def n_periods(self) -> int:
        return len(self.base_lookbacks)

    @property
    def n_sub_sleeves(self) -> int:
        return self.n_periods * self.lots

    def default_grid(self) -> list[Params]:
        grid: list[Params] = []
        for m in self.multipliers:
            lookbacks = [max(2, round(n * m)) for n in self.base_lookbacks]
            for target in self.vol_targets:
                if target is None:
                    grid.append({"lookbacks": lookbacks})
                    continue
                for window in self.vol_windows:
                    grid.append(
                        {"lookbacks": lookbacks, "vol_target": target, "vol_window": window}
                    )
        return grid

    def fit(self, params: Mapping[str, Any], is_bars: pd.DataFrame) -> Params:
        lookbacks = [int(n) for n in params["lookbacks"]]
        if len(lookbacks) != self.n_periods:
            raise ValueError(f"trend espera {self.n_periods} periodos, llegaron {lookbacks}")
        fitted: Params = {"lookbacks": lookbacks}
        if params.get("vol_target") is not None:
            if self.lots == 1:
                raise ValueError("vol_target necesita un adaptador con lots > 1 (trend_vol)")
            fitted["vol_target"] = float(params["vol_target"])
            fitted["vol_window"] = int(params.get("vol_window", DEFAULT_VOL_WINDOW))
        return fitted

    def exposures(self, bars: pd.DataFrame, fitted: Mapping[str, Any]) -> list[np.ndarray]:
        """Exposición por vela de cada periodo (0/1, o fraccional con `vol_target`)."""
        h, lo, c = (bars[col].to_numpy(dtype=float) for col in ("high", "low", "close"))
        states = [
            (donchian_states(h, lo, c, int(n), allow_short=False) == 1).astype(np.int8)
            for n in fitted["lookbacks"]
        ]
        if fitted.get("vol_target") is None:
            return [st.astype(float) for st in states]
        scale = vol_scale(realized_vol(c, int(fitted["vol_window"])), float(fitted["vol_target"]))
        return [sized_exposure(st, scale, self.rebalance_band) for st in states]

    def targets(self, bars: pd.DataFrame, fitted: Mapping[str, Any]) -> list[pd.Series]:
        """Una serie 0/1 por sub-sleeve; con lotes, en orden periodo-lote."""
        out = []
        for exposure in self.exposures(bars, fitted):
            if self.lots == 1:
                out.append(pd.Series(exposure, index=bars.index))
                continue
            level = np.floor(exposure * self.lots + 0.5)  # lotes comprados en cada vela
            out.extend(
                pd.Series((level > j).astype(float), index=bars.index) for j in range(self.lots)
            )
        return out

    def describe(self, fitted: Mapping[str, Any]) -> str:
        label = "/".join(str(n) for n in fitted["lookbacks"])
        if fitted.get("vol_target") is not None:
            label += f" vol {float(fitted['vol_target']):.0%}/{int(fitted['vol_window'])}d"
        return label


@dataclass
class TrendVolAdapter(TrendAdapter):
    """TrendAgent con volatility targeting (#83): `vol_target` y `vol_window` en la cuadrícula."""

    name: ClassVar[str] = "trend_vol"
    vol_targets: tuple[float | None, ...] = (0.4, 0.5, 0.6)
    vol_windows: tuple[int, ...] = (20, 30, 60)
    lots: int = 10


@dataclass
class IntradayMomentumAdapter:
    """Regla de timing de Shen, Urquhart y Wang en la última media hora (solo la pata larga)."""

    name: ClassVar[str] = "intraday_momentum"
    timeframe: ClassVar[Timeframe] = Timeframe.M30
    period: ClassVar[pd.Timedelta] = pd.Timedelta(minutes=30)
    n_sub_sleeves: ClassVar[int] = 1
    lots: ClassVar[int] = 1
    modes: tuple[str, ...] = ("onfh", "slh", "both")
    thresholds: tuple[float, ...] = (0.0, 0.0025, 0.005, 0.01)
    open_window: tuple[str, str] = DEFAULT_OPEN_WINDOW

    def default_grid(self) -> list[Params]:
        grid: list[Params] = []
        for mode in self.modes:
            # en `slh` el umbral no aplica (la regla no mira r_onfh)
            for threshold in (0.0,) if mode == "slh" else self.thresholds:
                grid.append({"mode": mode, "threshold": threshold})
        return grid

    def fit(self, params: Mapping[str, Any], is_bars: pd.DataFrame) -> Params:
        slot = params.get("open_slot")
        if slot is None:
            slot = volume_clock_open(is_bars, self.open_window) if len(is_bars) else 18
        return {
            "mode": str(params["mode"]),
            "threshold": float(params["threshold"]),
            "open_slot": int(slot),
        }

    def targets(self, bars: pd.DataFrame, fitted: Mapping[str, Any]) -> list[pd.Series]:
        sessions = session_table(bars, int(fitted["open_slot"]))
        mode, threshold = fitted["mode"], float(fitted["threshold"])
        long_days = [
            day
            for day, f, s in zip(sessions.index, sessions["r_onfh"], sessions["r_slh"], strict=True)
            if rule_is_long(f, s, mode, threshold)
        ]
        # Se compra al cierre de la vela de las 16:00 ET (16:30) y se vende al de las 16:30.
        target = pd.Series(0.0, index=bars.index)
        if long_days:
            # hora local (sin zona) y luego ET: así los días de cambio de horario no se recorren
            local = pd.DatetimeIndex(long_days) + self.period * PENULTIMATE_SLOT
            entry_bar = local.tz_localize(MARKET_TZ).tz_convert("UTC")
            target.loc[target.index.intersection(entry_bar)] = 1.0
        return [target]

    def describe(self, fitted: Mapping[str, Any]) -> str:
        return (
            f"{fitted['mode']} u={float(fitted['threshold']):.2%} "
            f"apertura {slot_label(int(fitted['open_slot']))} ET"
        )


ADAPTERS: dict[str, type[TrendAdapter] | type[IntradayMomentumAdapter]] = {
    "trend": TrendAdapter,
    "trend_vol": TrendVolAdapter,
    "intraday_momentum": IntradayMomentumAdapter,
}


def get_adapter(name: str) -> TrendAdapter | IntradayMomentumAdapter:
    try:
        return ADAPTERS[name]()
    except KeyError as exc:
        raise KeyError(f"estrategia sin adaptador de laboratorio: {name!r}") from exc


def param_key(fitted: Mapping[str, Any]) -> str:
    """Llave estable de un conjunto de parámetros (para contar estabilidad entre ventanas)."""
    return ";".join(
        f"{k}={v if not isinstance(v, list | tuple) else '/'.join(map(str, v))}"
        for k, v in sorted(fitted.items())
    )


__all__ = [
    "ADAPTERS",
    "IntradayMomentumAdapter",
    "Params",
    "StrategyAdapter",
    "TrendAdapter",
    "TrendVolAdapter",
    "get_adapter",
    "param_key",
]
