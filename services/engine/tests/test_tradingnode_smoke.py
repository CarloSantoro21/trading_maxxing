"""Smoke test del TradingNode con Binance Testnet (#11).

No hace ninguna llamada de red: usa un `FakeNode` como `node_factory` cuando hay credenciales
falsas y comprueba el modo OFFLINE cuando no las hay. Los logs estructurados JSON se verifican
leyendo las líneas que salen por el stream del handler.

Acceptance criteria (#11):
  [x] Con credenciales, el servicio pasa a `TESTNET` y construye el nodo (vía factory mockeado).
  [x] Sin credenciales, el servicio queda en `OFFLINE` con un motivo claro en el log.
  [x] Los logs son JSON para stdout (consumido por Azure App Service → Log Analytics /
      Application Insights, ver #29): `ts`, `level`, `service`, `message` y extras.
  [x] El log canónico `Starting TradingNode` y `Connected to Binance Testnet` se emite.

La conexión real contra https://testnet.binance.vision se hace a mano con las credenciales del
equipo (ver `services/engine/README.md`).
"""

from __future__ import annotations

import io
import json
import logging
import threading
from types import SimpleNamespace

import pytest
from tm_engine.logging_setup import JsonFormatter, setup_json_logging
from tm_engine.service import Credentials, EngineMode, EngineProfile, TradingEngineService


class FakeNode:
    """Imita lo que `TradingEngineService` usa del TradingNode, sin red ni event loop."""

    def __init__(self) -> None:
        self.stopped = threading.Event()
        self.disposed = False
        self.cache = SimpleNamespace(positions_open=lambda venue: [])

    def run(self, raise_exception: bool = False) -> None:
        self.stopped.wait(5)

    def get_event_loop(self):
        return None

    def stop(self) -> None:
        self.stopped.set()

    def dispose(self) -> None:
        self.disposed = True


@pytest.fixture
def json_log_stream():
    """Instala el handler JSON escribiendo a un buffer en memoria para inspeccionar las líneas."""
    buf = io.StringIO()
    root = logging.getLogger()
    before_handlers = list(root.handlers)
    before_level = root.level
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = setup_json_logging(service="engine", level="INFO", stream=buf)
    yield buf
    root.removeHandler(handler)
    for h in before_handlers:
        root.addHandler(h)
    root.setLevel(before_level)


def _read_json_lines(buf: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]


# --- logging estructurado --------------------------------------------------------------------


def test_json_formatter_emits_cloudwatch_compatible_fields():
    """Cada línea es un JSON con los campos obligatorios para Log Analytics / App Insights."""
    record = logging.LogRecord(
        name="tm_engine.service",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    record.symbol = "BTCUSDT"  # extra
    line = JsonFormatter(service="engine").format(record)
    payload = json.loads(line)
    assert payload["level"] == "INFO"
    assert payload["service"] == "engine"
    assert payload["logger"] == "tm_engine.service"
    assert payload["message"] == "hello world"
    assert payload["symbol"] == "BTCUSDT"
    # Timestamp ISO 8601 con milisegundos y zona UTC.
    assert payload["ts"].endswith("+00:00")


def test_setup_json_logging_is_idempotent(json_log_stream):
    before = len(logging.getLogger().handlers)
    setup_json_logging(service="engine", level="INFO", stream=json_log_stream)
    after = len(logging.getLogger().handlers)
    assert after == before  # no se duplica el handler


# --- modos del servicio ----------------------------------------------------------------------


def test_offline_mode_logs_clear_reason(json_log_stream):
    """Sin credenciales: queda en OFFLINE y el motivo aparece en los logs."""
    service = TradingEngineService(credentials=None)
    assert service.start() is EngineMode.OFFLINE
    status = service.status()
    assert status["mode"] == "offline"
    assert status["connected"] is False
    assert "BINANCE_TESTNET_API_KEY" in (status["error"] or "")
    lines = _read_json_lines(json_log_stream)
    offline = [ln for ln in lines if "sin conexión" in ln["message"]]
    assert offline, "se esperaba un log explicando por qué el motor arranca offline"
    assert offline[0]["level"] == "WARNING"
    assert "BINANCE_TESTNET_API_KEY" in offline[0]["message"]
    service.stop()


def test_testnet_mode_transitions_with_fake_node(json_log_stream):
    """Con credenciales y un factory simulado: el servicio pasa a TESTNET sin red real."""
    created: list[FakeNode] = []

    def factory(profile, credentials, strategy):
        assert credentials == Credentials("fake-key", "fake-secret")
        assert "BTCUSDT" in profile.symbols
        created.append(FakeNode())
        return created[-1]

    service = TradingEngineService(
        credentials=Credentials("fake-key", "fake-secret"),
        node_factory=factory,
    )
    try:
        assert service.start(EngineProfile(symbols=("BTCUSDT",))) is EngineMode.TESTNET
        assert service.status()["connected"] is True
        assert created, "el factory del nodo debía llamarse una vez"
    finally:
        service.stop()
    assert service.mode is EngineMode.STOPPED
    assert created[0].disposed

    # El issue #11 exige log canónico "Starting TradingNode" y "Connected to Binance Testnet".
    messages = [ln["message"] for ln in _read_json_lines(json_log_stream)]
    assert any(m == "Starting TradingNode" for m in messages)
    assert any(m == "Connected to Binance Testnet" for m in messages)


def test_cannot_start_twice():
    """`start` dos veces seguidas falla (ya está corriendo)."""
    service = TradingEngineService(credentials=None)
    assert service.start() is EngineMode.OFFLINE
    with pytest.raises(RuntimeError, match="ya está corriendo"):
        service.start()
    service.stop()


def test_build_node_imports_binance_adapter():
    """`build_node` arma la configuración real de Binance Spot TESTNET.

    No inicia el nodo (no `node.run`), solo valida que el adaptador exista y que la
    configuración apunte al venue y entorno correctos. En CI el import funciona porque
    `nautilus_trader` trae wheels para linux x86_64; en arm64 macOS se omite si falta Rust.
    """
    pytest.importorskip("nautilus_trader")
    from nautilus_trader.adapters.binance import BinanceAccountType  # type: ignore
    from nautilus_trader.adapters.binance.common.enums import BinanceEnvironment  # type: ignore
    from tm_engine.service import QueueDecisionSource, build_node, build_strategy

    profile = EngineProfile(log_level="ERROR")
    strategy = build_strategy(profile, QueueDecisionSource(), None)
    # Credenciales largas y falsas para que el adaptador acepte el formato.
    node = build_node(profile, Credentials("k" * 64, "s" * 64), strategy)
    try:
        data_cfg = node._config.data_clients["BINANCE"]
        exec_cfg = node._config.exec_clients["BINANCE"]
        for cfg in (data_cfg, exec_cfg):
            assert cfg.environment == BinanceEnvironment.TESTNET
            assert cfg.account_type == BinanceAccountType.SPOT
    finally:
        node.dispose()
