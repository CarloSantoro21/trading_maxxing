"""TradingEngineService: modo sin conexión, nodo simulado y construcción del TradingNode."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import fakeredis
import pytest
from tm_engine.decisions import publish_decision
from tm_engine.service import (
    CompositeDecisionSource,
    Credentials,
    EngineMode,
    EngineProfile,
    QueueDecisionSource,
    TradingEngineService,
    build_node,
    build_strategy,
)


class FakeNode:
    """Imita lo que el servicio usa de TradingNode, sin red ni event loop."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.stopped = threading.Event()
        self.disposed = False
        self.cache = SimpleNamespace(
            positions_open=lambda venue: [
                SimpleNamespace(
                    instrument_id="BTCUSDT.BINANCE",
                    side=SimpleNamespace(name="LONG"),
                    quantity="1",
                    avg_px_open="100",
                )
            ]
        )

    def run(self, raise_exception: bool = False) -> None:
        if self.fail:
            raise RuntimeError("sin red")
        self.stopped.wait(5)

    def get_event_loop(self):
        return None

    def stop(self) -> None:
        self.stopped.set()

    def dispose(self) -> None:
        self.disposed = True


def test_offline_without_keys(envelope):
    service = TradingEngineService()
    assert service.start() is EngineMode.OFFLINE
    status = service.status()
    assert status["mode"] == "offline"
    assert status["connected"] is False
    assert "BINANCE_TESTNET_API_KEY" in status["error"]
    env = service.submit(envelope().decision, sleeve="trend", id="d-1")
    assert env.id == "d-1"
    assert service.pending == 1
    assert service.get_positions() == {"sleeves": {}, "venue": []}
    with pytest.raises(RuntimeError):
        service.start()
    service.stop()
    assert service.mode is EngineMode.STOPPED
    service.stop()  # idempotente


def test_credentials_from_env(monkeypatch):
    assert Credentials.from_env() is None
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "k")
    monkeypatch.setenv("BINANCE_TESTNET_API_SECRET", "s")
    assert Credentials.from_env() == Credentials("k", "s")


def test_profile_from_env(monkeypatch):
    monkeypatch.setenv("ENGINE_SYMBOLS", "btcusdt, ETHUSDT")
    monkeypatch.setenv("ENGINE_TIMEFRAME", "15m")
    profile = EngineProfile.from_env()
    assert profile.symbols == ("BTCUSDT", "ETHUSDT")
    assert profile.timeframe == "15m"


def test_testnet_mode_with_fake_node(envelope):
    nodes: list[FakeNode] = []

    def factory(profile, credentials, strategy):
        assert credentials.api_key == "k"
        nodes.append(FakeNode())
        return nodes[-1]

    redis = fakeredis.FakeRedis()
    service = TradingEngineService(
        credentials=Credentials("k", "s"), redis_client=redis, node_factory=factory
    )
    assert service.start(EngineProfile(symbols=("BTCUSDT", "ETHUSDT"))) is EngineMode.TESTNET
    assert service.status()["connected"] is True
    assert service.get_positions()["venue"][0]["side"] == "LONG"
    # Las decisiones llegan tanto por submit como por el stream de Redis.
    service.submit(envelope().decision, sleeve="local")
    publish_decision(redis, envelope(sleeve="redis"))
    seen = [e.sleeve for e in service.strategy._source.poll(envelope().decision.decided_at)]
    assert sorted(seen) == ["local", "redis"]
    service.stop()
    assert nodes[0].disposed
    assert service.mode is EngineMode.STOPPED


def test_node_failure_is_reported():
    service = TradingEngineService(
        credentials=Credentials("k", "s"), node_factory=lambda *_: FakeNode(fail=True)
    )
    service.start()
    service._thread.join(2)
    assert "sin red" in service.status()["error"]
    assert service.status()["connected"] is False
    service.stop()


def test_stop_schedules_on_running_loop():
    calls = []

    class Loop:
        def is_running(self):
            return True

        def call_soon_threadsafe(self, fn):
            calls.append("scheduled")
            fn()

        def create_task(self, coro):
            coro.close()
            return SimpleNamespace(add_done_callback=lambda cb: cb(None))

    class LoopNode(FakeNode):
        def get_event_loop(self):
            return Loop()

        async def stop_async(self):  # pragma: no cover - se cierra sin ejecutarse
            self.stopped.set()

        def dispose(self):
            self.stopped.set()
            super().dispose()

    node = LoopNode()
    service = TradingEngineService(credentials=Credentials("k", "s"), node_factory=lambda *_: node)
    service.start()
    service.stop(timeout=1)
    assert calls == ["scheduled"]
    assert node.disposed


def test_redis_unavailable_falls_back_to_local_queue():
    class Broken:
        def exists(self, *_):
            raise ConnectionError("redis caído")

    service = TradingEngineService(redis_client=Broken())
    service.start()
    assert len(service.strategy._source.sources) == 1


def test_composite_source_survives_failures(envelope):
    class Failing:
        def poll(self, now):
            raise RuntimeError("boom")

    queue = QueueDecisionSource()
    queue.push(envelope())
    composite = CompositeDecisionSource([Failing(), queue])
    assert len(composite.poll(envelope().decision.decided_at)) == 1
    assert len(queue) == 0


def test_build_node_configures_binance_spot_testnet():
    from nautilus_trader.adapters.binance import BinanceAccountType
    from nautilus_trader.adapters.binance.common.enums import BinanceEnvironment

    profile = EngineProfile(log_level="ERROR")
    strategy = build_strategy(profile, QueueDecisionSource(), None)
    node = build_node(profile, Credentials("k" * 64, "s" * 64), strategy)
    try:
        exec_cfg = node._config.exec_clients["BINANCE"]
        data_cfg = node._config.data_clients["BINANCE"]
        for cfg in (exec_cfg, data_cfg):
            assert cfg.environment == BinanceEnvironment.TESTNET
            assert cfg.account_type == BinanceAccountType.SPOT
        assert strategy.config.manage_contingent_orders
        assert strategy.config.bar_types == ("BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL",)
    finally:
        node.dispose()
