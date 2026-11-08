"""Capa compartida del orquestador: señales, vetos y SimulationGate, **una vez para todos**.

En cada cierre de vela, para cada estrategia viva del registro (``active``, ``probation`` o
``shadow``, leídas de :class:`~tm_agents.registry.StrategyRegistry`) cuyo timeframe cerró, y
para cada símbolo:

1. arma el :class:`~tm_agents.base.AgentContext` con las velas cerradas hasta ``now``;
2. pide la :class:`~tm_shared.contracts.Signal` a la estrategia (Kronos se llama una vez por
   símbolo: su pronóstico se reutiliza para la señal y para las trayectorias del gate);
3. consulta el veto de noticias del símbolo (fail-open: si falla, no veta);
4. si la señal es ``long``, corre la SimulationGate con los niveles de la estrategia
   (:mod:`tm_agents.orchestrator.policy`) sobre el bootstrap histórico, combinado con las
   trayectorias de Kronos cuando aplica;
5. publica un :class:`~tm_agents.signals.SignalEvent` en ``tm:signals``.

Nada de esto depende del usuario: el tamaño, el riesgo y las órdenes son de la capa por bot
(:mod:`tm_agents.orchestrator.bot`). La misma clase corre en vivo (velas de
``MarketDataService``) y en backtest (velas de un DataFrame, :class:`FrameBarsProvider`).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

import pandas as pd
from tm_shared.contracts import Direction, OrderSide, Timeframe

from tm_agents.base import AgentContext, BaseAgent, registered_strategies
from tm_agents.orchestrator.policy import SleevePolicy, policy_for
from tm_agents.registry import StrategyRecord, StrategyRegistry
from tm_agents.signals import (
    LIVE_STATES,
    SignalEvent,
    SignalSink,
    StrategySnapshot,
    VetoInfo,
    catalog_from_records,
    event_id,
)
from tm_agents.simulation_gate import GateConfig, Levels, PathSet, SimulationGate

log = logging.getLogger(__name__)

TIMEFRAME_PERIOD: Mapping[Timeframe, pd.Timedelta] = {
    Timeframe.M1: pd.Timedelta(minutes=1),
    Timeframe.M15: pd.Timedelta(minutes=15),
    Timeframe.M30: pd.Timedelta(minutes=30),
    Timeframe.H4: pd.Timedelta(hours=4),
    Timeframe.D1: pd.Timedelta(days=1),
}

#: Historia que se le pasa a cada estrategia por timeframe (el Donchian de 200 días necesita
#: más de 200 velas; Kronos usa 400 velas de 4 h; el momentum intradía, 90 días de 30 min).
DEFAULT_LOOKBACK: Mapping[Timeframe, pd.Timedelta] = {
    Timeframe.M1: pd.Timedelta(days=2),
    Timeframe.M15: pd.Timedelta(days=30),
    Timeframe.M30: pd.Timedelta(days=120),
    Timeframe.H4: pd.Timedelta(days=100),
    Timeframe.D1: pd.Timedelta(days=500),
}

#: Comisión de Binance Spot por lado, descontada en la SimulationGate.
SPOT_FEE = 0.001


def bar_closed(now: datetime, timeframe: Timeframe) -> bool:
    """True si ``now`` es exactamente el cierre de una vela de ``timeframe`` (UTC)."""
    ts = _utc(now)
    return ts == ts.floor(TIMEFRAME_PERIOD[timeframe])


class BarsProvider(Protocol):
    """Velas **cerradas** hasta ``now`` (índice ``open_time`` UTC, columnas OHLCV)."""

    def bars(self, symbol: str, timeframe: Timeframe, now: datetime) -> pd.DataFrame: ...


class FrameBarsProvider:
    """Velas desde DataFrames en memoria (backtest y pruebas), sin look-ahead."""

    def __init__(
        self,
        frames: Mapping[tuple[str, Timeframe], pd.DataFrame],
        lookback: Mapping[Timeframe, pd.Timedelta] = DEFAULT_LOOKBACK,
    ) -> None:
        self._frames = {key: _with_utc_index(df) for key, df in frames.items()}
        self._lookback = lookback

    def bars(self, symbol: str, timeframe: Timeframe, now: datetime) -> pd.DataFrame:
        df = self._frames.get((symbol, timeframe))
        if df is None:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        ts = _utc(now)
        period = TIMEFRAME_PERIOD[timeframe]
        start = ts - self._lookback.get(timeframe, pd.Timedelta(days=30))
        closes = df.index + period
        return df[(closes <= ts) & (df.index >= start)]


class MarketDataBarsProvider:
    """Velas de ``MarketDataService.get_bars`` (disco primero y luego REST de Binance)."""

    def __init__(
        self, service: Any, lookback: Mapping[Timeframe, pd.Timedelta] = DEFAULT_LOOKBACK
    ) -> None:
        self._service = service
        self._lookback = lookback

    def bars(self, symbol: str, timeframe: Timeframe, now: datetime) -> pd.DataFrame:
        ts = _utc(now)
        start = ts - self._lookback.get(timeframe, pd.Timedelta(days=30))
        df = self._service.get_bars(symbol, timeframe.value, start, ts)
        return df[df.index + TIMEFRAME_PERIOD[timeframe] <= ts]


class VetoProvider(Protocol):
    """Lo que el orquestador usa del NewsAgent."""

    def active_veto(self, symbol: str) -> Any | None: ...


class StoreVetoProvider:
    """Lee los vetos que escribe el NewsAgent (Redis) sin necesitar el LLM ni CryptoPanic.

    Misma regla que ``NewsAgent.active_veto``: primero el veto global, luego el del activo
    base (``BTC`` para ``BTCUSDT``) y luego el de la moneda de cotización.
    """

    def __init__(self, store: Any) -> None:
        self.store = store

    def active_veto(self, symbol: str) -> Any | None:
        from tm_agents.news.agent import split_symbol
        from tm_agents.news.models import GLOBAL_SCOPE

        base, quote = split_symbol(symbol)
        for scope in (GLOBAL_SCOPE, base, quote):
            if scope and (veto := self.store.get(scope)) is not None:
                return veto
        return None


AgentFactory = Callable[[StrategyRecord], BaseAgent]


def default_agent_factory(record: StrategyRecord) -> BaseAgent:
    """Construye la estrategia registrada con los ``params`` de su fila en ``strategies``."""
    import tm_agents.strategies  # noqa: F401  (importar el paquete registra la biblioteca)

    registry = registered_strategies()
    if record.name not in registry:
        raise KeyError(f"estrategia sin código registrado: '{record.name}'")
    params = {k: v for k, v in dict(record.params).items() if k != "enabled"}
    return registry[record.name](**params)


class SharedSignalLayer:
    """Calcula y publica los eventos de todas las estrategias vivas (ver el módulo).

    Args:
        registry: estado de cada estrategia (se lee en cada ciclo).
        bars: proveedor de velas cerradas.
        sink: dónde se publica cada evento (Redis ``tm:signals`` o una lista).
        symbols: símbolos que se evalúan (``BTCUSDT``, ``ETHUSDT``).
        news: proveedor de vetos (``NewsAgent``); ``None`` = sin filtro de noticias.
        gate: SimulationGate (por omisión 10,000 trayectorias y comisión de 0.1 %).
        agents: estrategias ya construidas por nombre (p. ej. Kronos con un cliente propio);
            las que falten se construyen con ``agent_factory``.
        policies: políticas de niveles por estrategia que reemplazan a las de omisión.
    """

    def __init__(
        self,
        registry: StrategyRegistry,
        bars: BarsProvider,
        sink: SignalSink,
        *,
        symbols: Sequence[str] = ("BTCUSDT", "ETHUSDT"),
        news: VetoProvider | None = None,
        gate: SimulationGate | None = None,
        agents: Mapping[str, BaseAgent] | None = None,
        agent_factory: AgentFactory = default_agent_factory,
        policies: Mapping[str, SleevePolicy] | None = None,
    ) -> None:
        self.registry = registry
        self.bars = bars
        self.sink = sink
        self.symbols = tuple(symbols)
        self.news = news
        self.gate = gate or SimulationGate(GateConfig(fee_rate=SPOT_FEE))
        self._injected = dict(agents or {})
        self._factory = agent_factory
        self._policies = dict(policies or {})
        self._agents: dict[tuple[str, str], BaseAgent] = {}
        self.errors: list[str] = []

    # --- estrategias -----------------------------------------------------------------------
    def live_records(self) -> list[StrategyRecord]:
        return self.registry.list(*LIVE_STATES)

    def agent_for(self, record: StrategyRecord) -> BaseAgent:
        if record.name in self._injected:
            return self._injected[record.name]
        key = (record.name, json.dumps(dict(record.params), sort_keys=True, default=str))
        if key not in self._agents:
            self._agents[key] = self._factory(record)
        return self._agents[key]

    def timeframes(self) -> set[Timeframe]:
        """Timeframes de las estrategias vivas (para saber en qué instantes hay que correr)."""
        out: set[Timeframe] = set()
        for record in self.live_records():
            try:
                out.add(self.agent_for(record).timeframe)
            except Exception as exc:
                self._error(f"{record.name}: no se pudo construir: {exc}")
        return out

    # --- ciclo -----------------------------------------------------------------------------
    def on_bar_close(self, now: datetime) -> list[SignalEvent]:
        """Evalúa las estrategias cuyo timeframe cerró en ``now`` y publica sus eventos."""
        records = self.live_records()
        catalog = catalog_from_records(records)
        events: list[SignalEvent] = []
        for record in records:
            try:
                agent = self.agent_for(record)
            except Exception as exc:
                self._error(f"{record.name}: no se pudo construir: {exc}")
                continue
            if not bar_closed(now, agent.timeframe):
                continue
            for symbol in self.symbols:
                try:
                    event = self.evaluate(record, agent, symbol, now, catalog)
                except Exception as exc:  # una estrategia rota no detiene a las demás
                    self._error(f"{record.name} {symbol}: {exc}")
                    continue
                if event is None:
                    continue
                self.sink.publish(event)
                events.append(event)
        return events

    def evaluate(
        self,
        record: StrategyRecord,
        agent: BaseAgent,
        symbol: str,
        now: datetime,
        catalog: tuple[StrategySnapshot, ...] = (),
    ) -> SignalEvent | None:
        """Señal + veto + SimulationGate de una estrategia en un símbolo (sin publicar)."""
        ts = _utc(now)
        bars = self.bars.bars(symbol, agent.timeframe, ts.to_pydatetime())
        if bars is None or len(bars) == 0:
            log.info("%s %s: sin velas cerradas en %s", record.name, symbol, ts)
            return None
        ctx = AgentContext(symbol=symbol, now=ts.to_pydatetime(), bars={agent.timeframe: bars})
        signal = agent.analyze(ctx)
        price = Decimal(str(float(bars["close"].iloc[-1])))
        policy = policy_for(record.name, self._policies)
        stop_loss, take_profit, hard_stop = policy.levels(signal, price)

        sim = None
        sim_error = None
        if policy.use_gate and signal.direction is Direction.LONG and signal.confidence > 0:
            try:
                sim = self._simulate(agent, ctx, bars, price, stop_loss, take_profit, policy)
            except Exception as exc:
                sim_error = str(exc)
        return SignalEvent(
            id=event_id(record.name, symbol, ts.to_pydatetime()),
            strategy=record.name,
            state=record.state,
            symbol=symbol,
            bar_close=ts.to_pydatetime(),
            price=price,
            signal=signal,
            veto=self._veto(symbol),
            sim=sim,
            sim_error=sim_error,
            stop_loss=stop_loss,
            take_profit=take_profit,
            hard_stop=hard_stop,
            catalog=catalog,
        )

    def run(self, instants: Iterable[datetime]) -> list[SignalEvent]:
        """Corre ``on_bar_close`` en cada instante (backtest)."""
        out: list[SignalEvent] = []
        for now in instants:
            out.extend(self.on_bar_close(now))
        return out

    # --- pasos internos --------------------------------------------------------------------
    def _simulate(
        self,
        agent: BaseAgent,
        ctx: AgentContext,
        bars: pd.DataFrame,
        price: Decimal,
        stop_loss: Decimal,
        take_profit: Decimal,
        policy: SleevePolicy,
    ):
        external = None
        trajectories = getattr(agent, "trajectories", None)
        if policy.use_external_paths and callable(trajectories):
            try:
                external = PathSet.from_array(trajectories(ctx), source="kronos")
            except Exception as exc:  # sin Kronos se usa solo el bootstrap
                log.warning("trayectorias externas no disponibles: %s", exc)
        entry = float(price)
        paths = self.gate.build_paths(
            bars, entry=entry, horizon=policy.gate_horizon, external=external
        )
        levels = Levels(OrderSide.BUY, entry, float(stop_loss), float(take_profit))
        return self.gate.simulate(levels, paths)

    def _veto(self, symbol: str) -> VetoInfo | None:
        if self.news is None:
            return None
        try:
            veto = self.news.active_veto(symbol)
        except Exception as exc:  # fail-open, igual que el NewsAgent
            log.warning("fail-open: no se pudo consultar el veto de %s (%s)", symbol, exc)
            return None
        return VetoInfo.from_veto(veto) if veto is not None else None

    def _error(self, message: str) -> None:
        log.warning(message)
        self.errors.append(message)


def bar_close_instants(
    start: datetime, end: datetime, timeframes: Iterable[Timeframe]
) -> list[datetime]:
    """Cierres de vela de cualquiera de los ``timeframes`` en ``(start, end]`` (backtest)."""
    s, e = _utc(start), _utc(end)
    instants: set[pd.Timestamp] = set()
    for tf in timeframes:
        period = TIMEFRAME_PERIOD[tf]
        first = s.floor(period) + period
        instants.update(pd.date_range(first, e, freq=period))
    return [ts.to_pydatetime() for ts in sorted(instants)]


def _utc(value: datetime | pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _with_utc_index(df: pd.DataFrame) -> pd.DataFrame:
    idx = pd.DatetimeIndex(df.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    return df.set_axis(idx).sort_index()


__all__ = [
    "DEFAULT_LOOKBACK",
    "TIMEFRAME_PERIOD",
    "BarsProvider",
    "FrameBarsProvider",
    "MarketDataBarsProvider",
    "SharedSignalLayer",
    "VetoProvider",
    "bar_close_instants",
    "bar_closed",
    "default_agent_factory",
]
