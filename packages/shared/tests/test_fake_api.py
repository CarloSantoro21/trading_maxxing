"""La API simulada responde como el Worker: rutas, 404 con `not_found`, 422 y los CHECK."""

from __future__ import annotations

import pytest
from tm_shared.api_client import WorkerApiError
from tm_shared.fake_api import FakeWorkerApi


def _status(call) -> tuple[int | None, str | None]:
    with pytest.raises(WorkerApiError) as info:
        call()
    return info.value.status, info.value.code


@pytest.fixture
def api():
    fake = FakeWorkerApi()
    fake.c = fake.client(retries=0)
    return fake


def test_unknown_routes(api):
    assert _status(lambda: api.c.get("/api/me")) == (404, None)
    assert _status(lambda: api.c.get("/internal/nada")) == (404, None)
    assert _status(lambda: api.c.post("/internal/commands/x/otra", {})) == (404, None)


def test_strategies_errors(api):
    c = api.c
    assert _status(lambda: c.get("/internal/strategies/x")) == (404, "not_found")
    assert _status(lambda: c.post("/internal/strategies", {"name": "a", "x": 1})) == (422, None)
    c.post("/internal/strategies", {"name": "a"})
    assert _status(lambda: c.post("/internal/strategies", {"name": "a"})) == (
        409,
        "strategy_exists",
    )
    body = {"from_state": "research", "to_state": "lab", "actor": "system", "reason": "r"}
    assert _status(lambda: c.post("/internal/strategies/x/transitions", body)) == (
        404,
        "not_found",
    )
    bad = {**body, "extra": 1}
    assert _status(lambda: c.post("/internal/strategies/a/transitions", bad)) == (422, None)
    assert _status(lambda: c.patch("/internal/strategies/x", {"params": {}})) == (404, "not_found")
    assert _status(lambda: c.patch("/internal/strategies/a", {"state": "x"})) == (422, None)


def test_trades_errors_and_filters(api):
    c = api.c
    bad = {"bot_id": "b", "symbol": "BTCUSDT", "side": "buy", "nope": 1}
    assert _status(lambda: c.post("/internal/trades", bad)) == (422, None)
    bad = {"bot_id": "b", "symbol": "BTCUSDT", "side": "buy", "rationale": []}
    assert _status(lambda: c.post("/internal/trades", bad)) == (422, None)
    assert _status(lambda: c.patch("/internal/trades/x", {"pnl": 1})) == (404, "not_found")
    row = c.post(
        "/internal/trades",
        {"bot_id": "b", "symbol": "BTCUSDT", "side": "buy", "quantity": "1", "entry_price": 2},
    )
    assert row["quantity"] == 1.0
    assert _status(lambda: c.patch(f"/internal/trades/{row['id']}", {"x": 1})) == (422, None)
    c.patch(f"/internal/trades/{row['id']}", {"closed_at": "2026-01-01Z", "exit_price": "3"})
    assert len(c.get("/internal/trades", {"open": False})) == 1
    assert c.get("/internal/trades", {"open": True}) == []
    assert c.post("/internal/equity-snapshots", {"snapshots": [{}]}) == {"upserted": 1}


def test_reference_state_and_decisions(api):
    c = api.c
    assert _status(lambda: c.post("/internal/reference/equity", {"rows": []})) == (422, None)
    assert _status(lambda: c.put("/internal/reference/trades", {"points": []})) == (422, None)
    c.put("/internal/reference/equity", {"points": [{"strategy_id": "s", "day": "2026-01-01"}]})
    assert c.get("/internal/reference/equity", {"strategy_id": "s"})[0]["day"] == "2026-01-01"
    assert _status(lambda: c.post("/internal/decisions", {"decisions": [{"x": 1}]})) == (
        422,
        None,
    )
    assert _status(lambda: c.get("/internal/state/k")) == (404, "not_found")
    c.put("/internal/state/k", {"value": {"a": 1}})
    assert c.get("/internal/state/k")["value"] == {"a": 1}


def test_backtests_checks_and_heartbeat(api):
    c = api.c
    assert _status(lambda: c.patch("/internal/backtests/x", {"status": "done"})) == (
        404,
        "not_found",
    )
    job = api.add_backtest({"symbols": ["BTCUSDT"]})
    path = f"/internal/backtests/{job['id']}"
    assert _status(lambda: c.patch(path, {"nope": 1})) == (422, None)
    assert _status(lambda: c.patch(path, {"status": "done"})) == (422, "check_violation")
    assert _status(lambda: c.patch(path, {"status": "failed"})) == (422, "check_violation")
    assert _status(lambda: c.post("/internal/heartbeat", {"component": "Mal Nombre"})) == (
        422,
        None,
    )
    api.settings["news_enabled"] = True
    assert c.get("/internal/settings")["news_enabled"] is True
