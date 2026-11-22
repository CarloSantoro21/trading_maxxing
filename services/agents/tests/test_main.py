import json
import threading

import fakeredis
import pytest
import redis
from tm_agents import __main__ as worker


@pytest.fixture(autouse=True)
def _no_services(monkeypatch):
    """Ninguna prueba debe conectarse al Redis o a la API del desarrollador."""
    for var in ("REDIS_URL", "TM_API_URL", "TM_SERVICE_TOKEN", "SETTINGS_SOURCE"):
        monkeypatch.delenv(var, raising=False)


def test_once_prints_health(capsys):
    worker.main(["--once"])
    assert json.loads(capsys.readouterr().out)["service"] == "agents"


def test_loop_writes_heartbeat_and_stops(tmp_path, monkeypatch):
    hb = tmp_path / "hb"
    monkeypatch.setattr(worker, "HEARTBEAT", hb)
    monkeypatch.setattr(worker, "INTERVAL", 0.01)
    handlers = {}
    monkeypatch.setattr(worker.signal, "signal", lambda sig, fn: handlers.__setitem__(sig, fn))
    t = threading.Thread(target=worker.main, args=([],), daemon=True)
    t.start()
    while not hb.exists():
        pass
    handlers[worker.signal.SIGTERM]()
    t.join(timeout=2)
    assert not t.is_alive()
    assert float(hb.read_text()) > 0


def test_reference_service_needs_redis_and_api(monkeypatch):
    assert worker.reference_service_from_env() is None
    assert worker.reference_service_from_env({"REDIS_URL": "redis://x"}) is None
    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(lambda _url: fakeredis.FakeRedis()))
    svc = worker.reference_service_from_env(
        {
            "REDIS_URL": "redis://x",
            "TM_API_URL": "https://tm-api.test",
            "TM_SERVICE_TOKEN": "t",
        }
    )
    assert type(svc).__name__ == "ReferenceService"
    assert svc.source.last_id == "0-0"


def test_loop_runs_reference_cycle_and_survives_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "HEARTBEAT", tmp_path / "hb")
    monkeypatch.setattr(worker, "INTERVAL", 0.01)
    handlers = {}
    monkeypatch.setattr(worker.signal, "signal", lambda sig, fn: handlers.__setitem__(sig, fn))
    calls = []

    class Flaky:
        def run_once(self):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("sin red")
            if len(calls) >= 3:
                handlers[worker.signal.SIGTERM]()

    monkeypatch.setattr(worker, "reference_service_from_env", lambda: Flaky())
    worker.main([])
    assert len(calls) == 3


def test_loop_keeps_heartbeat_when_reference_setup_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "HEARTBEAT", tmp_path / "hb")
    monkeypatch.setattr(worker, "INTERVAL", 0.01)
    handlers = {}
    monkeypatch.setattr(worker.signal, "signal", lambda sig, fn: handlers.__setitem__(sig, fn))

    def broken():
        raise ConnectionError("redis caído")

    beats = []

    def beat():
        beats.append(1)
        handlers[worker.signal.SIGTERM]()

    monkeypatch.setattr(worker, "reference_service_from_env", broken)
    monkeypatch.setattr(worker, "beat", beat)
    worker.main([])
    assert beats == [1]


def test_reference_service_can_be_disabled():
    env = {
        "REDIS_URL": "redis://x",
        "TM_API_URL": "https://tm-api.test",
        "TM_SERVICE_TOKEN": "t",
        "REFERENCE_SERVICE": "0",
    }
    assert worker.reference_service_from_env(env) is None


def test_loop_runs_orchestrator_and_reference_together(tmp_path, monkeypatch):
    """Las tres piezas en el mismo proceso: el orquestador cada tick y la referencia cada
    heartbeat."""
    monkeypatch.setattr(worker, "HEARTBEAT", tmp_path / "hb")
    monkeypatch.setattr(worker, "INTERVAL", 0.0)
    monkeypatch.setattr(worker, "TICK", 0.0)
    handlers = {}
    monkeypatch.setattr(worker.signal, "signal", lambda sig, fn: handlers.__setitem__(sig, fn))
    calls = []

    class Worker:
        def tick(self):
            calls.append("tick")

    class Reference:
        def run_once(self):
            calls.append("reference")
            if calls.count("reference") == 2:
                handlers[worker.signal.SIGTERM]()

    monkeypatch.setattr(worker, "build_worker", lambda: Worker())
    monkeypatch.setattr(worker, "reference_service_from_env", lambda: Reference())
    worker.main([])
    assert calls == ["reference", "tick", "reference", "tick"]
    assert (tmp_path / "hb").exists()


def test_heartbeat_from_env_and_details():
    assert worker.heartbeat_from_env(None, {}) is None
    env = {
        "TM_API_URL": "https://tm-api.test",
        "TM_SERVICE_TOKEN": "t",
        "TM_HEARTBEAT_SECONDS": "3",
    }

    class Sup:
        def __init__(self):
            self.runners = {"b": 1, "a": 2}

    class W:
        supervisor = Sup()

    hb = worker.heartbeat_from_env(W(), env)
    assert hb.component == "agents" and hb.interval == 3
    assert hb._details() == {"orchestrator": True, "bots": ["a", "b"]}
    assert worker.heartbeat_from_env(None, env)._details() == {"orchestrator": False, "bots": []}


def test_loop_sends_heartbeat_to_api_and_stopping_on_exit(tmp_path, monkeypatch):
    from tm_shared.fake_api import FakeWorkerApi

    api = FakeWorkerApi()
    monkeypatch.setattr(worker, "HEARTBEAT", tmp_path / "hb")
    monkeypatch.setattr(worker, "INTERVAL", 0.01)
    monkeypatch.setattr(worker, "build_worker", lambda: None)
    monkeypatch.setattr(
        worker,
        "heartbeat_from_env",
        lambda w=None, env=None: worker.HeartbeatReporter(api.client(), "agents", interval=0),
    )
    handlers = {}
    monkeypatch.setattr(worker.signal, "signal", lambda sig, fn: handlers.__setitem__(sig, fn))
    t = threading.Thread(target=worker.main, args=([],), daemon=True)
    t.start()
    while "agents" not in api.heartbeats:
        pass
    handlers[worker.signal.SIGTERM]()
    t.join(timeout=5)
    assert not t.is_alive()
    assert api.heartbeats["agents"]["status"] == "stopping"


def test_loop_survives_heartbeat_setup_errors_and_stops_explainer(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "HEARTBEAT", tmp_path / "hb")
    monkeypatch.setattr(worker, "INTERVAL", 0.0)
    monkeypatch.setattr(worker, "TICK", 0.0)
    handlers = {}
    monkeypatch.setattr(worker.signal, "signal", lambda sig, fn: handlers.__setitem__(sig, fn))
    stopped = []

    class Loop:
        def stop(self):
            stopped.append(1)

    class W:
        explain_loop = Loop()

        def tick(self):
            handlers[worker.signal.SIGTERM]()

    def broken(_w=None, env=None):
        raise ValueError("TM_API_URL mal formada")

    monkeypatch.setattr(worker, "build_worker", lambda: W())
    monkeypatch.setattr(worker, "reference_service_from_env", lambda: None)
    monkeypatch.setattr(worker, "heartbeat_from_env", broken)
    worker.main([])
    assert stopped == [1]
