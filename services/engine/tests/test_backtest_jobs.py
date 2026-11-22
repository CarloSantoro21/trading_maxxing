"""Backtests encolados: `GET /internal/backtests?status=queued` + `PATCH` (API simulada)."""

from __future__ import annotations

import json
import threading

import httpx
import numpy as np
import pandas as pd
import pytest
from tm_engine.backtest_jobs import BacktestJobRunner, run_backtest_config
from tm_shared.fake_api import FakeWorkerApi

CONFIG = {
    "symbols": ["BTCUSDT"],
    "timeframe": "1d",
    "start": "2025-01-01",
    "end": "2025-06-30",
    "strategies": ["trend"],
    "initial_capital": 10_000,
}


def _loader(symbol, tf, start, end):
    """Velas sintéticas: tendencia con ruido, cada `tf` entre `start` y `end`."""
    freq = {"1d": "1D", "30m": "30min"}[tf]
    index = pd.date_range(start, end, freq=freq, inclusive="left", tz="UTC")
    rng = np.random.default_rng(7)
    close = 100 * np.exp(np.cumsum(0.002 + rng.normal(0, 0.02, len(index))))
    return pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": 1.0},
        index=index,
    )


def test_run_backtest_config_builds_a_report():
    report = run_backtest_config(CONFIG, loader=_loader)
    assert report["symbols"] == ["BTCUSDT"] and report["strategies"] == ["trend"]
    assert report["initial_capital"] == 10_000
    assert set(report["metrics"]) >= {"total_return", "sharpe", "max_drawdown", "final_equity"}
    assert report["benchmark"]["name"] == "buy_and_hold"
    assert report["equity"][0]["day"] == "2025-01-01"
    assert report["equity"][-1]["day"] == "2025-06-30"
    assert report["by_strategy"]["trend"]["timeframe"] == "1d"
    assert report["trades"] >= 0


def test_run_backtest_config_validates():
    with pytest.raises(ValueError, match="símbolo"):
        run_backtest_config({**CONFIG, "symbols": []}, loader=_loader)
    with pytest.raises(ValueError, match="posterior"):
        run_backtest_config({**CONFIG, "end": "2024-01-01"}, loader=_loader)
    with pytest.raises(ValueError, match="no hay velas"):
        run_backtest_config(CONFIG, loader=lambda *_: pd.DataFrame())
    with pytest.raises(KeyError):
        run_backtest_config({**CONFIG, "strategies": ["nope"]}, loader=_loader)


def test_runner_marks_running_then_done():
    api = FakeWorkerApi()
    job = api.add_backtest(CONFIG)
    api.add_backtest(CONFIG, status="done")
    runner = BacktestJobRunner(api.client(), lambda cfg: {"ok": True, "n": len(cfg["symbols"])})
    assert runner.poll() == 1
    row = api.backtests[job["id"]]
    assert row["status"] == "done" and row["report"] == {"ok": True, "n": 1}
    assert row["started_at"] and row["finished_at"]
    patches = api.calls("PATCH", f"/internal/backtests/{job['id']}")
    assert [json.loads(p.content)["status"] for p in patches] == [
        "running",
        "done",
    ]
    q = api.calls("GET", "/internal/backtests")[0].url.params
    assert q["status"] == "queued"
    assert runner.done == 1


def test_runner_records_failures_and_throttles():
    api = FakeWorkerApi()
    job = api.add_backtest(CONFIG)
    now = [0.0]

    def boom(_cfg):
        raise ValueError("sin datos")

    runner = BacktestJobRunner(api.client(), boom, interval=60, clock=lambda: now[0])
    assert runner.poll() == 1
    assert api.backtests[job["id"]]["status"] == "failed"
    assert api.backtests[job["id"]]["error"] == "ValueError: sin datos"
    api.add_backtest(CONFIG)
    now[0] = 30
    assert runner.poll() == 0  # todavía no toca
    assert runner.failed == 1


def test_runner_survives_api_errors():
    api = FakeWorkerApi()
    api.fail[("GET", "/internal/backtests")] = [httpx.Response(401, json={"detail": "x"})]
    runner = BacktestJobRunner(api.client(retries=0), lambda cfg: {})
    assert runner.poll() == 0
    job = api.add_backtest(CONFIG)
    path = f"/internal/backtests/{job['id']}"
    api.fail[("PATCH", path)] = [httpx.Response(404, json={"detail": "no", "code": "not_found"})]
    runner._last = float("-inf")
    assert runner.poll() == 0  # no se pudo tomar: no se corre
    assert api.backtests[job["id"]]["status"] == "queued"
    # el resultado no se puede guardar: se registra y se sigue
    api.fail[("PATCH", path)] = [httpx.Response(200, json={}), httpx.Response(500, json={})]
    runner._last = float("-inf")
    assert runner.poll() == 1


def test_runner_thread_stops():
    api = FakeWorkerApi()
    api.add_backtest(CONFIG)
    seen = threading.Event()

    def run(cfg):
        seen.set()
        return {"ok": True}

    stop = threading.Event()
    thread = BacktestJobRunner(api.client(), run).start(stop, tick=0.01)
    assert seen.wait(2)
    stop.set()
    thread.join(2)
    assert not thread.is_alive()


def test_runner_thread_survives_unexpected_errors(caplog):
    calls = threading.Event()

    class Boom(BacktestJobRunner):
        def poll(self):
            calls.set()
            raise RuntimeError("inesperado")

    stop = threading.Event()
    thread = Boom(FakeWorkerApi().client()).start(stop, tick=0.01)
    assert calls.wait(2)
    stop.set()
    thread.join(2)
    assert "falló el ciclo de backtests" in caplog.text


def test_long_range_with_naive_index_is_subsampled():
    def naive(symbol, tf, start, end):
        frame = _loader(symbol, tf, start, end)
        return frame.set_axis(frame.index.tz_localize(None))

    config = {**CONFIG, "start": "2023-01-01", "end": "2025-06-30", "strategies": []}
    report = run_backtest_config(config, loader=naive)
    assert report["strategies"] == ["trend"]  # por defecto
    assert len(report["equity"]) <= 402
    assert report["equity"][-1]["day"] == "2025-06-30"
