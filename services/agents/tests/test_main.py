import json
import threading

import fakeredis
import pytest
import redis
from tm_agents import __main__ as worker


@pytest.fixture(autouse=True)
def _no_services(monkeypatch):
    """Ninguna prueba debe conectarse al Redis o al Supabase del desarrollador."""
    for var in ("REDIS_URL", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
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


def test_reference_service_needs_redis_and_supabase(monkeypatch):
    assert worker.reference_service_from_env() is None
    assert worker.reference_service_from_env({"REDIS_URL": "redis://x"}) is None
    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(lambda _url: fakeredis.FakeRedis()))
    svc = worker.reference_service_from_env(
        {
            "REDIS_URL": "redis://x",
            "SUPABASE_URL": "http://supabase.test",
            "SUPABASE_SERVICE_ROLE_KEY": "sb_secret_x",
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
