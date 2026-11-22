import json
import threading

from tm_engine import __main__ as worker
from tm_shared.fake_api import FakeWorkerApi


def test_once_prints_health(capsys):
    worker.main(["--once"])
    out = json.loads(capsys.readouterr().out)
    assert out["service"] == "engine"
    assert out["mode"] == "offline"


def test_loop_writes_heartbeat_and_stops(tmp_path, monkeypatch):
    hb = tmp_path / "hb"
    monkeypatch.setattr(worker, "HEARTBEAT", hb)
    monkeypatch.setattr(worker, "INTERVAL", 0.01)
    handlers = {}
    monkeypatch.setattr(worker.signal, "signal", lambda sig, fn: handlers.__setitem__(sig, fn))
    t = threading.Thread(target=worker.main, args=([],), daemon=True)
    t.start()
    while not hb.exists() or not hb.read_text():
        pass
    handlers[worker.signal.SIGTERM]()
    t.join(timeout=2)
    assert not t.is_alive()
    beat = json.loads(hb.read_text())
    assert beat["ts"] > 0
    assert beat["mode"] == "offline"
    assert beat["connected"] is False


API = {"TM_API_URL": "https://tm-api.test", "TM_SERVICE_TOKEN": "secret"}


def test_redis_and_recorder_from_env(monkeypatch):
    assert worker.redis_from_env() is None
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    assert worker.redis_from_env() is not None  # el cliente es perezoso: no conecta aquí
    assert worker.api_from_env({}) is None
    assert worker.recorder_from_env(None) is None
    api = worker.api_from_env(API)
    assert api.base_url == "https://tm-api.test"
    assert worker.recorder_from_env(api, {"ENGINE_BOT_ID": "bot-1"}).bot_id == "bot-1"
    assert worker.recorder_from_env(api, {}).bot_id is None


def test_sleeve_guard_from_env():
    assert worker.sleeve_guard_from_env(None) is None
    guard = worker.sleeve_guard_from_env(worker.api_from_env(API))
    assert type(guard).__name__ == "TradingSleeveGuard"


def test_backtests_and_heartbeat_from_env():
    api = worker.api_from_env(API)
    assert worker.backtests_from_env(None, {}) is None
    assert worker.backtests_from_env(api, {"BACKTEST_JOBS": "0"}) is None
    runner = worker.backtests_from_env(api, {"BACKTEST_POLL_SECONDS": "5"})
    assert runner.interval == 5
    assert worker.heartbeat_from_env(None, object(), {}) is None

    class Svc:
        def status(self):
            return {"mode": "offline"}

    hb = worker.heartbeat_from_env(api, Svc(), {"TM_HEARTBEAT_SECONDS": "7"})
    assert hb.component == "engine" and hb.interval == 7
    assert worker._engine_status({"error": None, "restore_error": None}) == "ok"
    assert worker._engine_status({"error": "x"}) == "degraded"


def test_loop_with_api_sends_heartbeats_and_stops(tmp_path, monkeypatch):
    """Con la API configurada, el bucle manda el latido y uno final con `stopping`."""
    api = FakeWorkerApi()
    monkeypatch.setattr(worker, "HEARTBEAT", tmp_path / "hb")
    monkeypatch.setattr(worker, "INTERVAL", 0.01)
    monkeypatch.setattr(worker, "api_from_env", lambda env=None: api.client())
    started = []

    class Jobs:
        def start(self, stop):
            started.append(stop)

    monkeypatch.setattr(worker, "backtests_from_env", lambda api=None, env=None: Jobs())
    handlers = {}
    monkeypatch.setattr(worker.signal, "signal", lambda sig, fn: handlers.__setitem__(sig, fn))
    t = threading.Thread(target=worker.main, args=([],), daemon=True)
    t.start()
    while "engine" not in api.heartbeats:
        pass
    handlers[worker.signal.SIGTERM]()
    t.join(timeout=5)
    assert not t.is_alive()
    assert api.heartbeats["engine"]["status"] == "stopping"
    assert api.heartbeats["engine"]["details"]["mode"] == "offline"
    assert len(started) == 1 and started[0].is_set()  # el hilo de backtests se detiene
