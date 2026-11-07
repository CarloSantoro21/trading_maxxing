"""SimulationGate: Monte Carlo antes de cada operación (propuesta, sección 3, paso 5).

Para una decisión con stop-loss (SL) y take-profit (TP) estima, sobre un conjunto de
trayectorias de precio, la probabilidad de que se toque primero el TP, primero el SL o
de que la posición siga abierta al final del horizonte, y el valor esperado en unidades
de R (1R = distancia entre la entrada y el stop). Si el valor esperado no es positivo,
:meth:`SimulationGate.approve` bloquea la operación.

Fuentes de trayectorias:

* **Bootstrap histórico** (:func:`bootstrap_paths`): 10,000 trayectorias armadas
  remuestreando con reemplazo los retornos de velas reales de la *misma hora del día*
  (UTC) dentro de los últimos 60 días. Cada paso futuro toma una vela cuya hora coincide
  con la hora de ese paso, así se conserva la estacionalidad intradía de la volatilidad.
  Todo es vectorizado con numpy y con semilla fija.
* **Trayectorias externas** (:meth:`PathSet.from_array`): por ejemplo las de Kronos
  (``(samples, pasos)`` de precios de cierre, o ``(samples, pasos, OHLC[V])``). Se
  combinan con el bootstrap con :func:`combine`, que asigna a cada fuente un peso
  total configurable.

Regla de la vela ambigua: dentro de una vela no se sabe si el máximo ocurrió antes que
el mínimo. Si en la misma vela se tocan el SL y el TP, se cuenta como **SL** (regla
conservadora: nunca sobreestima el valor esperado).

Ejemplo::

    gate = SimulationGate()
    paths = gate.historical_paths(bars, entry=60_000.0, horizon=96)   # velas de 15m
    paths = combine(paths, PathSet.from_array(kronos_paths, source="kronos"), weight=0.3)
    sim = gate.simulate(decision, paths)
    verdict = gate.approve(sim)          # RiskVerdict(approved=sim.expected_value > 0)
    decision = attach_sim(decision, sim) # queda guardado en decision.rationale.sim
"""

from __future__ import annotations

import math
import numbers
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol

import numpy as np
import pandas as pd
from numpy.typing import ArrayLike, NDArray
from tm_shared.contracts import OrderSide, RiskVerdict, SimResult, TradeDecision

Seed = int | np.random.Generator

DEFAULT_N_PATHS = 10_000
DEFAULT_LOOKBACK_DAYS = 60
# Mínimo de velas históricas para que el bootstrap tenga sentido
MIN_HISTORY_BARS = 30


class DecisionLike(Protocol):
    """Lo mínimo que se necesita de una decisión: lado, entrada, stop y objetivo."""

    @property
    def side(self) -> OrderSide: ...

    @property
    def entry_price(self) -> Decimal | float | None: ...

    @property
    def stop_loss(self) -> Decimal | float: ...

    @property
    def take_profit(self) -> Decimal | float: ...


@dataclass(frozen=True, slots=True)
class Levels:
    """Niveles de una operación sin el resto de la ``TradeDecision`` (útil en pruebas)."""

    side: OrderSide
    entry_price: float
    stop_loss: float
    take_profit: float


@dataclass(frozen=True, slots=True, eq=False)
class PathSet:
    """Trayectorias de precio con forma ``(n_paths, steps)`` por columna.

    ``high`` y ``low`` son los extremos de cada vela simulada y ``close`` su cierre. Los
    ``weights`` (uno por trayectoria, suman 1) permiten mezclar fuentes de distinto tamaño.
    El precio de entrada no forma parte de la matriz: el paso 0 es la primera vela futura.
    """

    high: NDArray[np.float64]
    low: NDArray[np.float64]
    close: NDArray[np.float64]
    source: str
    weights: NDArray[np.float64] = field(default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        shapes = {self.high.shape, self.low.shape, self.close.shape}
        if len(shapes) != 1 or self.close.ndim != 2:
            raise ValueError("high, low y close deben ser matrices 2D con la misma forma")
        n, steps = self.close.shape
        if n < 1 or steps < 1:
            raise ValueError("se necesita al menos una trayectoria con al menos un paso")
        for arr in (self.high, self.low, self.close):
            if not np.all(np.isfinite(arr)) or np.any(arr <= 0):
                raise ValueError("las trayectorias deben tener precios finitos y positivos")
        if np.any(self.high < self.low):
            raise ValueError("hay velas con high < low")
        if self.weights is None:
            object.__setattr__(self, "weights", np.full(n, 1.0 / n))
        else:
            w = np.asarray(self.weights, dtype=np.float64)
            if w.shape != (n,) or np.any(w < 0) or not np.isfinite(w).all() or w.sum() <= 0:
                raise ValueError("weights debe tener un valor no negativo por trayectoria")
            object.__setattr__(self, "weights", w / w.sum())

    @property
    def n_paths(self) -> int:
        return int(self.close.shape[0])

    @property
    def steps(self) -> int:
        return int(self.close.shape[1])

    @classmethod
    def from_array(cls, paths: ArrayLike, *, source: str = "kronos") -> PathSet:
        """Construye un ``PathSet`` desde un arreglo externo.

        Acepta ``(samples, pasos)`` con precios de cierre (high = low = close, la
        detección queda a cierre de vela) o ``(samples, pasos, k)`` con columnas en orden
        OHLC[V], como devuelve ``KronosForecastService`` (``Forecast.paths``). En el caso
        3D, high y low se amplían para contener open y close, por si el modelo genera
        velas incoherentes.
        """
        arr = np.asarray(paths, dtype=np.float64)
        if arr.ndim == 2:
            close = np.ascontiguousarray(arr)
            return cls(high=close, low=close, close=close, source=source)
        if arr.ndim == 3 and arr.shape[2] >= 4:
            o, h, lo, c = (arr[:, :, i] for i in range(4))
            high = np.maximum.reduce([h, o, c])
            low = np.minimum.reduce([lo, o, c])
            return cls(high=high, low=low, close=np.ascontiguousarray(c), source=source)
        raise ValueError("paths debe tener forma (samples, pasos) o (samples, pasos, OHLC[V])")


def combine(base: PathSet, external: PathSet, *, weight: float) -> PathSet:
    """Mezcla dos fuentes: ``external`` recibe ``weight`` del peso total y ``base`` el resto.

    El peso es por fuente, no por trayectoria: 20 trayectorias de Kronos con
    ``weight=0.3`` pesan lo mismo en conjunto que el 30 % de la distribución, aunque el
    bootstrap tenga 10,000. Si los horizontes difieren se recorta al más corto.
    """
    if not _is_real(weight) or not 0.0 <= weight <= 1.0:
        raise ValueError("weight debe estar en el intervalo [0, 1]")
    steps = min(base.steps, external.steps)
    w = np.concatenate([base.weights * (1.0 - weight), external.weights * weight])
    return PathSet(
        high=np.concatenate([base.high[:, :steps], external.high[:, :steps]]),
        low=np.concatenate([base.low[:, :steps], external.low[:, :steps]]),
        close=np.concatenate([base.close[:, :steps], external.close[:, :steps]]),
        source="combined",
        weights=w,
    )


def bootstrap_paths(
    bars: pd.DataFrame,
    *,
    entry: float,
    horizon: int,
    n_paths: int = DEFAULT_N_PATHS,
    seed: Seed = 42,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    start: datetime | pd.Timestamp | None = None,
) -> PathSet:
    """Trayectorias por bootstrap de velas históricas de la misma hora del día.

    Args:
        bars: velas con índice ``open_time`` (UTC) y columnas ``high, low, close`` (el
            formato de ``MarketDataService.get_bars``). Se usan solo las de los últimos
            ``lookback_days`` días antes de ``start``.
        entry: precio de arranque de todas las trayectorias.
        horizon: número de velas futuras a simular.
        n_paths: número de trayectorias.
        seed: semilla entera o ``numpy.random.Generator``.
        lookback_days: ventana histórica en días.
        start: ``open_time`` de la primera vela futura. Por omisión, la vela siguiente a
            la última de ``bars``.

    Para cada vela histórica se toman sus retornos relativos al cierre anterior
    (``high/prev_close``, ``low/prev_close`` y ``close/prev_close``); cada paso simulado
    aplica esos tres factores al cierre simulado previo, de modo que la vela conserva su
    rango intravela real.
    """
    _validate_positive_int("horizon", horizon)
    _validate_positive_int("n_paths", n_paths)
    _validate_positive_int("lookback_days", lookback_days)
    if not _is_real(entry) or not math.isfinite(entry) or entry <= 0:
        raise ValueError("entry debe ser un número finito mayor a 0")
    missing = {"high", "low", "close"} - set(bars.columns)
    if missing:
        raise ValueError(f"a bars le faltan columnas: {sorted(missing)}")
    if not isinstance(bars.index, pd.DatetimeIndex) or len(bars) < 2:
        raise ValueError("bars debe tener un DatetimeIndex con al menos 2 velas")

    index = bars.index if bars.index.tz is not None else bars.index.tz_localize("UTC")
    df = bars.set_axis(index).sort_index()
    step = _bar_step(df.index)
    start_ts = df.index[-1] + step if start is None else _to_utc(start)
    df = df[(df.index >= start_ts - pd.Timedelta(days=lookback_days)) & (df.index < start_ts)]

    prev_close = df["close"].shift(1).to_numpy(dtype=np.float64)[1:]
    hours_hist = df.index.hour.to_numpy()[1:]
    factors = np.stack(
        [
            df["high"].to_numpy(dtype=np.float64)[1:] / prev_close,
            df["low"].to_numpy(dtype=np.float64)[1:] / prev_close,
            df["close"].to_numpy(dtype=np.float64)[1:] / prev_close,
        ],
        axis=1,
    )
    ok = np.isfinite(factors).all(axis=1) & (factors > 0).all(axis=1)
    factors, hours_hist = factors[ok], hours_hist[ok]
    if factors.shape[0] < MIN_HISTORY_BARS:
        raise ValueError(
            f"historia insuficiente: {factors.shape[0]} velas en la ventana "
            f"(se requieren al menos {MIN_HISTORY_BARS})"
        )

    # Pool por hora del día: velas ordenadas por hora, con offset y conteo de cada hora.
    order = np.argsort(hours_hist, kind="stable")
    pool, pool_hours = factors[order], hours_hist[order]
    counts = np.bincount(pool_hours, minlength=24)
    offsets = np.concatenate([[0], np.cumsum(counts)[:-1]])
    # Hora (UTC) de cada paso futuro; si una hora no tiene velas, se usa todo el pool.
    step_hours = pd.DatetimeIndex(start_ts + step * np.arange(horizon)).hour.to_numpy()
    step_counts = counts[step_hours]
    empty = step_counts == 0
    step_offsets = np.where(empty, 0, offsets[step_hours])
    step_counts = np.where(empty, pool.shape[0], step_counts)

    rng = _make_rng(seed)
    u = rng.random((n_paths, horizon))
    idx = step_offsets + (u * step_counts).astype(np.int64)
    sampled = pool[idx]  # (n_paths, horizon, 3)

    log_close = np.cumsum(np.log(sampled[:, :, 2]), axis=1)
    close = entry * np.exp(log_close)
    prev = np.empty_like(close)
    prev[:, 0] = entry
    prev[:, 1:] = close[:, :-1]
    return PathSet(
        high=prev * sampled[:, :, 0],
        low=prev * sampled[:, :, 1],
        close=close,
        source="bootstrap",
    )


@dataclass(frozen=True, slots=True)
class GateConfig:
    """Parámetros del SimulationGate.

    Attributes:
        n_paths: trayectorias del bootstrap histórico.
        lookback_days: ventana de velas históricas.
        seed: semilla fija del bootstrap.
        external_weight: peso total de las trayectorias externas (Kronos) al combinar.
        fee_rate: comisión por lado (0.001 = 0.1 % de Binance Spot); se descuenta del
            resultado de cada trayectoria en unidades de R.
        min_expected_value: el valor esperado debe ser estrictamente mayor (en R).
    """

    n_paths: int = DEFAULT_N_PATHS
    lookback_days: int = DEFAULT_LOOKBACK_DAYS
    seed: int = 42
    external_weight: float = 0.5
    fee_rate: float = 0.0
    min_expected_value: float = 0.0

    def __post_init__(self) -> None:
        _validate_positive_int("n_paths", self.n_paths)
        _validate_positive_int("lookback_days", self.lookback_days)
        if not 0.0 <= self.external_weight <= 1.0:
            raise ValueError("external_weight debe estar en el intervalo [0, 1]")
        if not 0.0 <= self.fee_rate < 0.1:
            raise ValueError("fee_rate debe estar en el intervalo [0, 0.1)")
        if not math.isfinite(self.min_expected_value):
            raise ValueError("min_expected_value debe ser finito")


class SimulationGate:
    """Monte Carlo #2 de la propuesta: bloquea operaciones con valor esperado ≤ 0."""

    def __init__(self, config: GateConfig | None = None) -> None:
        self.config = config or GateConfig()

    def historical_paths(
        self,
        bars: pd.DataFrame,
        *,
        entry: float,
        horizon: int,
        start: datetime | pd.Timestamp | None = None,
    ) -> PathSet:
        """Bootstrap histórico con los parámetros de la configuración."""
        return bootstrap_paths(
            bars,
            entry=entry,
            horizon=horizon,
            n_paths=self.config.n_paths,
            seed=self.config.seed,
            lookback_days=self.config.lookback_days,
            start=start,
        )

    def build_paths(
        self,
        bars: pd.DataFrame,
        *,
        entry: float,
        horizon: int,
        external: PathSet | ArrayLike | None = None,
        start: datetime | pd.Timestamp | None = None,
    ) -> PathSet:
        """Bootstrap histórico, combinado con ``external`` (p. ej. Kronos) si se da."""
        paths = self.historical_paths(bars, entry=entry, horizon=horizon, start=start)
        if external is None:
            return paths
        ext = external if isinstance(external, PathSet) else PathSet.from_array(external)
        return combine(paths, ext, weight=self.config.external_weight)

    def simulate(
        self, decision: DecisionLike, paths: PathSet, *, entry: float | None = None
    ) -> SimResult:
        """Probabilidades de TP primero, SL primero y abierta al final, y valor esperado en R.

        ``entry`` sobrescribe ``decision.entry_price`` (obligatorio si la decisión no lo
        trae, por ejemplo en una orden a mercado).
        """
        return simulate(decision, paths, entry=entry, fee_rate=self.config.fee_rate)

    def approve(self, sim: SimResult) -> RiskVerdict:
        """PASS si el valor esperado supera ``min_expected_value`` (0 por omisión)."""
        threshold = self.config.min_expected_value
        if sim.expected_value > threshold:
            return RiskVerdict(approved=True)
        return RiskVerdict(
            approved=False,
            reasons=[
                f"SimulationGate: valor esperado de {sim.expected_value:+.3f}R "
                f"(se requiere mayor a {threshold:+.3f}R; P(TP)={sim.p_take_profit:.1%}, "
                f"P(SL)={sim.p_stop_loss:.1%}, {sim.n_paths} trayectorias {sim.source})"
            ],
        )

    def evaluate(
        self, decision: TradeDecision, paths: PathSet, *, entry: float | None = None
    ) -> tuple[TradeDecision, RiskVerdict]:
        """Simula, guarda el resultado en ``decision.rationale.sim`` y devuelve el veredicto."""
        sim = self.simulate(decision, paths, entry=entry)
        return attach_sim(decision, sim), self.approve(sim)


def simulate(
    decision: DecisionLike,
    paths: PathSet,
    *,
    entry: float | None = None,
    fee_rate: float = 0.0,
) -> SimResult:
    """Evalúa la decisión sobre cada trayectoria (vectorizado). Ver :class:`SimulationGate`.

    Por trayectoria: TP primero vale ``+reward/risk`` R, SL primero vale ``-1`` R y si
    sigue abierta al final vale el P&L del último cierre en R. Si en la misma vela se
    tocan SL y TP se cuenta como SL (regla conservadora). La comisión de ida y vuelta
    (``2 * fee_rate * entry``) se resta de cada resultado.
    """
    raw_entry = decision.entry_price if entry is None else entry
    if raw_entry is None:
        raise ValueError("la decisión no tiene entry_price: pásalo con entry=")
    entry_f = float(raw_entry)
    sl = float(decision.stop_loss)
    tp = float(decision.take_profit)
    if not all(math.isfinite(x) and x > 0 for x in (entry_f, sl, tp)):
        raise ValueError("entry, stop_loss y take_profit deben ser finitos y positivos")
    is_buy = OrderSide(decision.side) is OrderSide.BUY
    if is_buy and not sl < entry_f < tp:
        raise ValueError("en una compra: stop_loss < entry < take_profit")
    if not is_buy and not tp < entry_f < sl:
        raise ValueError("en una venta: take_profit < entry < stop_loss")
    if not 0.0 <= fee_rate < 0.1:
        raise ValueError("fee_rate debe estar en el intervalo [0, 0.1)")

    risk = abs(entry_f - sl)
    reward_r = abs(tp - entry_f) / risk
    if is_buy:
        hit_tp, hit_sl = paths.high >= tp, paths.low <= sl
        open_r = (paths.close[:, -1] - entry_f) / risk
    else:
        hit_tp, hit_sl = paths.low <= tp, paths.high >= sl
        open_r = (entry_f - paths.close[:, -1]) / risk

    never = paths.steps  # índice "nunca se tocó"
    first_tp = np.where(hit_tp.any(axis=1), hit_tp.argmax(axis=1), never)
    first_sl = np.where(hit_sl.any(axis=1), hit_sl.argmax(axis=1), never)
    sl_first = (first_sl < never) & (first_sl <= first_tp)  # empate en la vela -> SL
    tp_first = (first_tp < never) & ~sl_first
    still_open = ~(sl_first | tp_first)

    outcome_r = np.where(tp_first, reward_r, np.where(sl_first, -1.0, open_r))
    outcome_r -= 2.0 * fee_rate * entry_f / risk
    w = paths.weights
    # Se recortan a [0, 1] por el error de redondeo de las sumas con pesos
    p_tp = min(1.0, float(w[tp_first].sum()))
    p_sl = min(1.0 - p_tp, float(w[sl_first].sum()))
    p_open = max(0.0, 1.0 - p_tp - p_sl) if still_open.any() else 0.0
    return SimResult(
        p_take_profit=p_tp,
        p_stop_loss=p_sl,
        p_open=p_open,
        expected_value=float(np.dot(w, outcome_r)),
        n_paths=paths.n_paths,
        source=paths.source,
    )


def attach_sim(decision: TradeDecision, sim: SimResult) -> TradeDecision:
    """Copia de la decisión con ``rationale.sim`` lleno (los contratos son inmutables)."""
    rationale = decision.rationale.model_copy(update={"sim": sim})
    return decision.model_copy(update={"rationale": rationale})


def _bar_step(index: pd.DatetimeIndex) -> pd.Timedelta:
    diffs = index[1:] - index[:-1]
    step = pd.Series(diffs).mode().iloc[0]
    if step <= pd.Timedelta(0):
        raise ValueError("bars tiene timestamps duplicados")
    return step


def _to_utc(value: datetime | pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _validate_positive_int(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, numbers.Integral):
        raise TypeError(f"{name} debe ser un entero, no {type(value).__name__}")
    if value < 1:
        raise ValueError(f"{name} debe ser mayor o igual a 1")


def _is_real(value: object) -> bool:
    return isinstance(value, numbers.Real) and not isinstance(value, bool)


def _make_rng(seed: Seed) -> np.random.Generator:
    if isinstance(seed, np.random.Generator):
        return seed
    if isinstance(seed, bool) or not isinstance(seed, numbers.Integral):
        raise TypeError("seed debe ser un entero o un numpy.random.Generator")
    return np.random.default_rng(int(seed))


__all__ = [
    "DecisionLike",
    "GateConfig",
    "Levels",
    "PathSet",
    "SimulationGate",
    "attach_sim",
    "bootstrap_paths",
    "combine",
    "simulate",
]
