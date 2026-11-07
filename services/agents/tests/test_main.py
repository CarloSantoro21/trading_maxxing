import json
import threading

from tm_agents import __main__ as worker


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
