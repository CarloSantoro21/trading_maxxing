import json
import threading

from tm_engine import __main__ as worker


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


def test_redis_and_recorder_from_env(monkeypatch):
    assert worker.redis_from_env() is None
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    assert worker.redis_from_env() is not None  # el cliente es perezoso: no conecta aquí
    monkeypatch.delenv("ENGINE_BOT_ID", raising=False)
    assert worker.recorder_from_env() is None
    monkeypatch.setenv("ENGINE_BOT_ID", "bot-1")
    monkeypatch.setenv("SUPABASE_URL", "http://supabase.test")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "secret")
    assert worker.recorder_from_env().bot_id == "bot-1"
