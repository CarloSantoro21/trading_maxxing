"""TradingEngineService: TradingNode de Nautilus con Binance Spot en modo TESTNET.

- `start(profile)`: arma el nodo con el adaptador de Binance Spot (`BinanceEnvironment.TESTNET`)
  y `AgenticStrategy`, y lo corre en un hilo propio. Las credenciales salen de
  `BINANCE_TESTNET_API_KEY` y `BINANCE_TESTNET_API_SECRET`; si faltan, el servicio queda en
  modo `offline` ("sin conexión"): no se conecta a nada, acepta decisiones y lo reporta en el
  heartbeat.
- `submit(decision)`: encola una decisión para la estrategia (además de las que llegan por el
  stream de Redis `tm:decisions`, ver `tm_engine.decisions`).
- `get_positions()`: posiciones y P&L por sleeve, más las posiciones netas del venue.
- `stop()`: detiene el nodo y libera sus recursos.

Modo de ejecución (`execution`):

- `testnet`: el `TradingNode` de arriba (requiere las llaves).
- `sim`: sin Nautilus ni llaves, `tm_engine.sim.SimExecutionEngine` llena cada decisión al
  precio actual de la REST pública de Binance con la comisión del laboratorio y registra los
  fills por la API igual que el modo real (demo local, ver `docs/correr-local.md`).
- `offline`: no ejecuta nada; solo acepta decisiones y lo reporta.
- `auto` (por omisión de la clase): `testnet` con llaves, `offline` sin ellas. El proceso
  (`python -m tm_engine`) resuelve `ENGINE_EXECUTION=auto` a `sim` cuando faltan las llaves
  (ver `resolve_execution`).
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from tm_shared.contracts import Timeframe, TradeDecision

from tm_engine.decisions import DecisionEnvelope, DecisionSource, RedisStreamDecisionSource
from tm_engine.instruments import BINANCE, bar_type, instrument_id
from tm_engine.recorder import FillRecorder, MemoryFillRecorder
from tm_engine.sim import PublicPriceFeed, SimExecutionEngine, SimRunner
from tm_engine.strategy import AgenticStrategy, AgenticStrategyConfig

log = logging.getLogger(__name__)

API_KEY_ENV = "BINANCE_TESTNET_API_KEY"
API_SECRET_ENV = "BINANCE_TESTNET_API_SECRET"


class EngineMode(StrEnum):
    STOPPED = "stopped"
    OFFLINE = "offline"  # sin llaves de Binance Testnet: no hay conexión al venue
    TESTNET = "testnet"
    SIM = "sim"  # fills simulados al precio público de Binance (tm_engine.sim)


class ExecutionMode(StrEnum):
    """Cómo se ejecutan las decisiones (`ENGINE_EXECUTION`)."""

    AUTO = "auto"
    TESTNET = "testnet"
    SIM = "sim"
    OFFLINE = "offline"


EXECUTION_ENV = "ENGINE_EXECUTION"


def resolve_execution(
    env: Mapping[str, str] | None = None, credentials: Credentials | object | None = None
) -> ExecutionMode:
    """`ENGINE_EXECUTION`; `auto` (o vacío) es `testnet` con llaves y `sim` sin ellas."""
    env = os.environ if env is None else env
    raw = (env.get(EXECUTION_ENV) or "auto").strip().lower()
    try:
        mode = ExecutionMode(raw)
    except ValueError:
        raise ValueError(
            f"{EXECUTION_ENV}={raw!r} no es válido (auto, testnet, sim u offline)"
        ) from None
    if mode is ExecutionMode.AUTO:
        creds = Credentials.from_env() if credentials is None else credentials
        return ExecutionMode.TESTNET if isinstance(creds, Credentials) else ExecutionMode.SIM
    return mode


class EngineProfile(BaseModel):
    """Perfil de ejecución del motor (qué operar y cómo)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = "default"
    trader_id: str = Field(default="TM-ENGINE-001", pattern=r"^[A-Za-z0-9]+-[0-9]{3}$")
    symbols: tuple[str, ...] = ("BTCUSDT",)
    timeframe: Timeframe = Timeframe.M1
    emulation_trigger: str = "BID_ASK"
    poll_interval_secs: float = Field(default=1.0, gt=0)
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> EngineProfile:
        symbols = os.environ.get("ENGINE_SYMBOLS", "BTCUSDT")
        return cls(
            name=os.environ.get("ENGINE_PROFILE", "default"),
            symbols=tuple(s.strip().upper() for s in symbols.split(",") if s.strip()),
            timeframe=Timeframe(os.environ.get("ENGINE_TIMEFRAME", "1m")),
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        )


@dataclass(frozen=True)
class Credentials:
    api_key: str
    api_secret: str

    @classmethod
    def from_env(cls) -> Credentials | None:
        key = os.environ.get(API_KEY_ENV, "").strip()
        secret = os.environ.get(API_SECRET_ENV, "").strip()
        return cls(key, secret) if key and secret else None


class QueueDecisionSource:
    """Cola en memoria, segura entre hilos, para `TradingEngineService.submit`."""

    def __init__(self) -> None:
        self._queue: deque[DecisionEnvelope] = deque()
        self._lock = threading.Lock()

    def push(self, envelope: DecisionEnvelope) -> None:
        with self._lock:
            self._queue.append(envelope)

    def poll(self, now: datetime | None = None) -> list[DecisionEnvelope]:
        with self._lock:
            out = list(self._queue)
            self._queue.clear()
        return out

    def __len__(self) -> int:
        with self._lock:
            return len(self._queue)


class CompositeDecisionSource:
    """Junta varias fuentes; si una falla (p. ej. Redis caído) sigue con las demás."""

    def __init__(self, sources: Iterable[DecisionSource]) -> None:
        self.sources = list(sources)

    def poll(self, now: datetime) -> list[DecisionEnvelope]:
        out: list[DecisionEnvelope] = []
        for source in self.sources:
            try:
                out.extend(source.poll(now))
            except Exception as exc:
                log.warning("fuente de decisiones falló: %s", exc)
        return out


def build_strategy(
    profile: EngineProfile,
    source: DecisionSource,
    recorder: FillRecorder,
    sleeve_guard: Callable[[str], bool] | None = None,
) -> AgenticStrategy:
    config = AgenticStrategyConfig(
        bar_types=tuple(str(bar_type(s, profile.timeframe)) for s in profile.symbols),
        emulation_trigger=profile.emulation_trigger,
        poll_interval_secs=profile.poll_interval_secs,
        manage_contingent_orders=True,
    )
    return AgenticStrategy(config, source=source, recorder=recorder, sleeve_guard=sleeve_guard)


def build_node(profile: EngineProfile, credentials: Credentials, strategy: AgenticStrategy) -> Any:
    """TradingNode con datos y ejecución de Binance Spot en TESTNET."""
    from nautilus_trader.adapters.binance import (
        BINANCE,
        BinanceAccountType,
        BinanceDataClientConfig,
        BinanceExecClientConfig,
        BinanceLiveDataClientFactory,
        BinanceLiveExecClientFactory,
    )
    from nautilus_trader.adapters.binance.common.enums import BinanceEnvironment
    from nautilus_trader.config import (
        InstrumentProviderConfig,
        LiveExecEngineConfig,
        LoggingConfig,
        TradingNodeConfig,
    )
    from nautilus_trader.live.node import TradingNode
    from nautilus_trader.model.identifiers import TraderId

    instruments = InstrumentProviderConfig(
        load_ids=frozenset(instrument_id(s) for s in profile.symbols)
    )
    common = {
        "api_key": credentials.api_key,
        "api_secret": credentials.api_secret,
        "account_type": BinanceAccountType.SPOT,
        "environment": BinanceEnvironment.TESTNET,
        "instrument_provider": instruments,
    }
    config = TradingNodeConfig(
        trader_id=TraderId(profile.trader_id),
        # Stdout → Azure App Service → Log Analytics / Application Insights (ver #29).
        logging=LoggingConfig(log_level=profile.log_level, log_colors=False),
        exec_engine=LiveExecEngineConfig(reconciliation=True),
        data_clients={BINANCE: BinanceDataClientConfig(**common)},
        exec_clients={BINANCE: BinanceExecClientConfig(**common)},
    )
    # Loop propio: el nodo corre en el hilo `tm-engine-node`, no en el que lo construye.
    node = TradingNode(config=config, loop=asyncio.new_event_loop())
    node.add_data_client_factory(BINANCE, BinanceLiveDataClientFactory)
    node.add_exec_client_factory(BINANCE, BinanceLiveExecClientFactory)
    node.build()
    node.trader.add_strategy(strategy)
    return node


NodeFactory = Callable[[EngineProfile, Credentials, AgenticStrategy], Any]
_FROM_ENV = object()


class TradingEngineService:
    """Fachada del motor de ejecución en vivo (Binance Spot Testnet)."""

    def __init__(
        self,
        *,
        credentials: Credentials | object | None = _FROM_ENV,
        redis_client: Any | None = None,
        recorder: FillRecorder | None = None,
        node_factory: NodeFactory = build_node,
        sleeve_guard: Callable[[str], bool] | None = None,
        execution: ExecutionMode | str = ExecutionMode.AUTO,
        price_feed: PublicPriceFeed | None = None,
    ) -> None:
        self._credentials = Credentials.from_env() if credentials is _FROM_ENV else credentials
        self.execution = ExecutionMode(execution)
        self._price_feed = price_feed
        self.sim: SimExecutionEngine | None = None
        self._sim_runner: SimRunner | None = None
        self._redis = redis_client
        self._recorder: FillRecorder = recorder or MemoryFillRecorder()
        self._node_factory = node_factory
        # Solo los sleeves con capital (active/probation) envían órdenes; shadow nunca.
        self._sleeve_guard = sleeve_guard
        self._queue = QueueDecisionSource()
        self.mode = EngineMode.STOPPED
        self.profile: EngineProfile | None = None
        self.strategy: AgenticStrategy | None = None
        self.error: str | None = None
        self.restored = 0
        self.restore_error: str | None = None
        self._node: Any | None = None
        self._thread: threading.Thread | None = None

    # --- ciclo de vida -----------------------------------------------------------------

    def start(self, profile: EngineProfile | None = None) -> EngineMode:
        if self.mode is not EngineMode.STOPPED:
            raise RuntimeError(f"el motor ya está corriendo ({self.mode})")
        self.profile = profile or EngineProfile()
        self.error = None
        sources: list[DecisionSource] = [self._queue]
        if self._redis is not None:
            try:
                sources.append(RedisStreamDecisionSource(self._redis))
            except Exception as exc:
                log.warning("Redis no disponible, solo se usarán decisiones locales: %s", exc)
        source = CompositeDecisionSource(sources)
        if self.execution is ExecutionMode.SIM:
            return self._start_sim(source)
        self.strategy = build_strategy(self.profile, source, self._recorder, self._sleeve_guard)
        self.restored = self._restore_positions(self.strategy)
        if self.execution is ExecutionMode.OFFLINE:
            self.mode = EngineMode.OFFLINE
            self.error = f"{EXECUTION_ENV}=offline: no se ejecuta ninguna orden"
            log.warning("motor en modo sin conexión: %s", self.error)
            return self.mode
        if self._credentials is None:
            self.mode = EngineMode.OFFLINE
            self.error = f"faltan {API_KEY_ENV} / {API_SECRET_ENV}"
            log.warning("motor en modo sin conexión: %s", self.error)
            return self.mode
        assert isinstance(self._credentials, Credentials)
        log.info(
            "Starting TradingNode",
            extra={
                "venue": "BINANCE",
                "environment": "testnet",
                "symbols": list(self.profile.symbols),
                "timeframe": self.profile.timeframe,
            },
        )
        self._node = self._node_factory(self.profile, self._credentials, self.strategy)
        self._thread = threading.Thread(target=self._run_node, name="tm-engine-node", daemon=True)
        self._thread.start()
        self.mode = EngineMode.TESTNET
        # Mensaje histórico (en español) + mensaje canónico para el issue #11 (en inglés).
        log.info("motor conectado a Binance Spot Testnet (%s)", ",".join(self.profile.symbols))
        log.info(
            "Connected to Binance Testnet",
            extra={"symbols": list(self.profile.symbols), "timeframe": self.profile.timeframe},
        )
        return self.mode

    def _start_sim(self, source: DecisionSource) -> EngineMode:
        """Ejecución simulada: sin TradingNode, fills al precio público de Binance."""
        assert self.profile is not None
        fee = os.environ.get("ENGINE_SIM_FEE")
        self.sim = SimExecutionEngine(
            source,
            self._recorder,
            prices=self._price_feed or PublicPriceFeed(),
            sleeve_guard=self._sleeve_guard,
            **({"fee_rate": Decimal(fee)} if fee else {}),
        )
        self.restored = self._restore_positions(self.sim)
        self._sim_runner = SimRunner(self.sim, interval=self.profile.poll_interval_secs)
        self._sim_runner.start()
        self.mode = EngineMode.SIM
        log.warning(
            "motor en modo SIMULADO: fills al precio público de Binance con comisión de %s "
            "(sin órdenes en Testnet; pon BINANCE_TESTNET_API_KEY/SECRET para operar de verdad)",
            self.sim.fee_rate,
        )
        log.info(
            "Starting simulated execution",
            extra={"symbols": list(self.profile.symbols), "fee": str(self.sim.fee_rate)},
        )
        return self.mode

    def _restore_positions(self, strategy: AgenticStrategy | SimExecutionEngine) -> int:
        """Siembra el libro por bot y sleeve con las operaciones abiertas de `trades` (#89).

        Si el recorder no sabe leerlas (memoria) no hay nada que restaurar. Si falla la
        lectura, el motor arranca igual (las entradas siguen funcionando) pero lo reporta en
        `status()["restore_error"]`: los cierres de posiciones previas se rechazarían.
        """
        self.restore_error = None
        load = getattr(self._recorder, "open_positions", None)
        if not callable(load):
            return 0
        try:
            count = strategy.restore_positions(load())
        except Exception as exc:
            self.restore_error = f"no se pudieron restaurar las posiciones de trades: {exc}"
            log.error(self.restore_error)
            return 0
        log.info("posiciones restauradas desde trades: %d", count)
        return count

    def _run_node(self) -> None:
        node = self._node
        try:
            loop = node.get_event_loop()
            if loop is not None:
                asyncio.set_event_loop(loop)
            node.run(raise_exception=True)
        except Exception as exc:
            self.error = f"el nodo terminó con error: {exc}"
            log.exception("el TradingNode terminó con error")

    def stop(self, timeout: float = 30.0) -> None:
        if self.mode is EngineMode.STOPPED:
            return
        if self._sim_runner is not None:
            self._sim_runner.stop(timeout)
            self._sim_runner = None
        node, self._node = self._node, None
        if node is not None:
            loop = node.get_event_loop()
            if loop is not None and loop.is_running():
                done = threading.Event()

                def _schedule() -> None:
                    task = loop.create_task(node.stop_async())
                    task.add_done_callback(lambda _t: done.set())

                loop.call_soon_threadsafe(_schedule)
                done.wait(timeout)
            else:
                node.stop()
            if self._thread is not None:
                self._thread.join(timeout)
            node.dispose()
        self._thread = None
        self.mode = EngineMode.STOPPED

    # --- decisiones y consultas ------------------------------------------------------------

    def submit(
        self, decision: TradeDecision, sleeve: str = "", id: str | None = None
    ) -> DecisionEnvelope:
        """Encola una decisión; la estrategia la toma en su siguiente ciclo de sondeo."""
        envelope = DecisionEnvelope(id=id, sleeve=sleeve, decision=decision)
        self._queue.push(envelope)
        return envelope

    @property
    def pending(self) -> int:
        return len(self._queue)

    def get_positions(self) -> dict[str, Any]:
        """Posiciones por sleeve (libro propio) y posiciones netas del venue (cache del nodo)."""
        engine = self.sim if self.sim is not None else self.strategy
        sleeves = engine.sleeve_report() if engine is not None else {}
        venue: list[dict[str, str]] = []
        if self._node is not None:
            for position in self._node.cache.positions_open(venue=BINANCE):
                venue.append(
                    {
                        "instrument_id": str(position.instrument_id),
                        "side": str(position.side.name),
                        "quantity": str(position.quantity),
                        "avg_px_open": str(position.avg_px_open),
                    }
                )
        return {"sleeves": sleeves, "venue": venue}

    def status(self) -> dict[str, Any]:
        """Estado para el heartbeat y el healthcheck."""
        engine = self.sim if self.sim is not None else self.strategy
        error = self.error
        if error is None and self._sim_runner is not None:
            error = self._sim_runner.error
        status: dict[str, Any] = {
            "mode": self.mode.value,
            "connected": self.mode is EngineMode.TESTNET and self.error is None,
            "profile": self.profile.name if self.profile else None,
            "symbols": list(self.profile.symbols) if self.profile else [],
            "pending_decisions": self.pending,
            "rejections": len(engine.rejections) if engine is not None else 0,
            "error": error,
            "restored_positions": self.restored,
            "restore_error": self.restore_error,
        }
        if self.sim is not None:
            status["sim"] = {
                "fills": self.sim.fills,
                "fee": str(self.sim.fee_rate),
                "price_source": self.sim.prices.source,
                "positions": self.sim.positions(),
            }
        return status
