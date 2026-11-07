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
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from tm_shared.contracts import Timeframe, TradeDecision

from tm_engine.decisions import DecisionEnvelope, DecisionSource, RedisStreamDecisionSource
from tm_engine.instruments import BINANCE, bar_type, instrument_id
from tm_engine.recorder import FillRecorder, MemoryFillRecorder
from tm_engine.strategy import AgenticStrategy, AgenticStrategyConfig

log = logging.getLogger(__name__)

API_KEY_ENV = "BINANCE_TESTNET_API_KEY"
API_SECRET_ENV = "BINANCE_TESTNET_API_SECRET"


class EngineMode(StrEnum):
    STOPPED = "stopped"
    OFFLINE = "offline"  # sin llaves de Binance Testnet: no hay conexión al venue
    TESTNET = "testnet"


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
    profile: EngineProfile, source: DecisionSource, recorder: FillRecorder
) -> AgenticStrategy:
    config = AgenticStrategyConfig(
        bar_types=tuple(str(bar_type(s, profile.timeframe)) for s in profile.symbols),
        emulation_trigger=profile.emulation_trigger,
        poll_interval_secs=profile.poll_interval_secs,
        manage_contingent_orders=True,
    )
    return AgenticStrategy(config, source=source, recorder=recorder)


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
        # Logs a stdout (los recoge Docker / CloudWatch).
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
    ) -> None:
        self._credentials = Credentials.from_env() if credentials is _FROM_ENV else credentials
        self._redis = redis_client
        self._recorder: FillRecorder = recorder or MemoryFillRecorder()
        self._node_factory = node_factory
        self._queue = QueueDecisionSource()
        self.mode = EngineMode.STOPPED
        self.profile: EngineProfile | None = None
        self.strategy: AgenticStrategy | None = None
        self.error: str | None = None
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
        self.strategy = build_strategy(
            self.profile, CompositeDecisionSource(sources), self._recorder
        )
        if self._credentials is None:
            self.mode = EngineMode.OFFLINE
            self.error = f"faltan {API_KEY_ENV} / {API_SECRET_ENV}"
            log.warning("motor en modo sin conexión: %s", self.error)
            return self.mode
        assert isinstance(self._credentials, Credentials)
        self._node = self._node_factory(self.profile, self._credentials, self.strategy)
        self._thread = threading.Thread(target=self._run_node, name="tm-engine-node", daemon=True)
        self._thread.start()
        self.mode = EngineMode.TESTNET
        log.info("motor conectado a Binance Spot Testnet (%s)", ",".join(self.profile.symbols))
        return self.mode

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
        sleeves = self.strategy.sleeve_report() if self.strategy is not None else {}
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
        return {
            "mode": self.mode.value,
            "connected": self.mode is EngineMode.TESTNET and self.error is None,
            "profile": self.profile.name if self.profile else None,
            "symbols": list(self.profile.symbols) if self.profile else [],
            "pending_decisions": self.pending,
            "rejections": len(self.strategy.rejections) if self.strategy else 0,
            "error": self.error,
        }
