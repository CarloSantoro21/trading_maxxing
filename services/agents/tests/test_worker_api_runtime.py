"""Piezas en vivo que hablan con la API de Workers: explainer en su hilo, auditoría de
decisiones y equity de los bots (contra ``tm_shared.fake_api``, sin red)."""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import UTC, datetime
from decimal import Decimal

import httpx
from tm_agents.explainer import ExplainerAgent
from tm_agents.explainer_runtime import (
    EXPLANATION_KIND,
    ExplainerLoop,
    WorkerApiExplanationSink,
)
from tm_agents.orchestrator.bot import BotConfig, BotProfile
from tm_agents.orchestrator.equity import EquitySnapshotReporter
from tm_agents.orchestrator.streams import FanoutSink, MemorySink, WorkerApiDecisionSink
from tm_agents.orchestrator.supervisor import BotSupervisor, InMemoryBotRepository
from tm_shared.fake_api import FakeWorkerApi

BOT = "7f0c4c1e-5a4b-4f3e-9a51-0d9b1b2c3d4e"


def _wait(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "tiempo agotado"
        time.sleep(0.005)


# --- ExplainerLoop ---------------------------------------------------------------------------
def test_explainer_loop_runs_coroutines_off_thread():
    loop = ExplainerLoop().start()
    assert loop.start() is loop  # idempotente
    seen: list[str] = []

    async def job(name):
        await asyncio.sleep(0.01)
        seen.append(threading.current_thread().name)

    future = loop.submit(job("a"))
    assert future is not None
    assert loop.drain(2)
    assert seen == ["explainer-loop"] and loop.pending == 0
    loop.stop()
    assert not loop.running
    loop.stop()  # dos veces no truena


def test_explainer_loop_drops_when_stopped_or_full(caplog):
    loop = ExplainerLoop(max_pending=1)

    async def noop():
        return None

    assert loop.submit(noop()) is None and loop.dropped == 1  # sin arrancar
    loop.start()
    gate = threading.Event()

    async def blocked():
        await asyncio.to_thread(gate.wait, 2)

    assert loop.submit(blocked()) is not None
    assert loop(noop()) is None and loop.dropped == 2  # lleno: se descarta
    gate.set()
    assert loop.drain(2)

    async def boom():
        raise RuntimeError("falló")

    loop.submit(boom())
    assert loop.drain(2)
    loop.stop()
    assert "explicación fallida" in caplog.text


# --- sinks hacia /internal/decisions ---------------------------------------------------------
def test_explanation_sink_posts_kind_explanation():
    api = FakeWorkerApi()
    sink = WorkerApiExplanationSink(api.client())
    sink(BOT, "env-1", "Compré porque la tendencia es alcista.")
    sink("bot-dev", "env-2", "texto")  # id que no es UUID: va en el payload
    for_bot = sink.for_bot(BOT)
    for_bot("env-3", "otro")
    assert sink.saved == 3
    first, second, third = api.decisions
    assert first["kind"] == EXPLANATION_KIND and first["bot_id"] == BOT
    assert first["payload"] == {
        "envelope_id": "env-1",
        "text": "Compré porque la tendencia es alcista.",
    }
    assert "bot_id" not in second and second["payload"]["bot_id"] == "bot-dev"
    assert third["payload"]["envelope_id"] == "env-3"
    api.fail[("POST", "/internal/decisions")] = [httpx.Response(400, json={"detail": "x"})]
    sink(BOT, "env-4", "x")  # nunca lanza
    assert sink.failed == 1


def test_decision_sink_and_fanout(make_bot, make_event):
    api = FakeWorkerApi()
    runner, memory, _ = make_bot(BOT)
    audit = WorkerApiDecisionSink(api.client(), kind="decision")
    fan = FanoutSink(memory, audit)
    runner.sink = fan
    (envelope,) = runner.on_event(make_event(1.0))
    assert memory.items == [envelope]
    (row,) = api.decisions
    assert row["kind"] == "decision" and row["bot_id"] == BOT
    assert row["symbol"] == envelope.decision.symbol and row["strategy"] == envelope.sleeve
    assert row["payload"]["id"] == envelope.id
    # la API cae: la decisión llega igual a Redis (el primer sink) y no se lanza
    api.fail[("POST", "/internal/decisions")] = [httpx.Response(500, json={})]
    assert fan.publish(envelope) is not None
    assert len(memory.items) == 2
    assert audit.failed == 1

    class Broken:
        def publish(self, item):
            raise RuntimeError("roto")

    assert FanoutSink(MemorySink(), Broken()).publish(envelope) == "1"


def test_bot_runner_uses_executor_and_sink_end_to_end(make_bot, make_event):
    """En vivo: sin event loop, la explicación corre en el ExplainerLoop y llega a la API."""
    api = FakeWorkerApi()
    loop = ExplainerLoop().start()
    try:
        cfg = make_bot(BOT)[0].config
        sup = BotSupervisor(
            InMemoryBotRepository([cfg]),
            MemorySink(),
            explainer=ExplainerAgent(),  # sin LLM: plantilla determinista
            explanation_sink=WorkerApiExplanationSink(api.client()),
            explain_executor=loop,
        )
        sup.sync()
        (envelope,) = sup.on_event(make_event(1.0))
        assert loop.drain(2)
        _wait(lambda: len(api.decisions) == 1)
        (row,) = api.decisions
        assert row["kind"] == "explanation" and row["bot_id"] == BOT
        assert row["payload"]["envelope_id"] == envelope.id and row["payload"]["text"]
    finally:
        loop.stop()


def test_bot_runner_executor_failure_does_not_block(make_bot, make_event):
    from tm_agents.orchestrator.bot import BotRunner

    def broken(coro):
        coro.close()
        raise RuntimeError("sin hilo")

    base, sink, _ = make_bot()
    runner = BotRunner(base.config, sink, explainer=ExplainerAgent(), explain_executor=broken)
    assert runner.on_event(make_event(1.0))  # la decisión sale igual


# --- equity ----------------------------------------------------------------------------------
def test_equity_reporter_sends_rounded_snapshots_and_throttles():
    api = FakeWorkerApi()
    profile = BotProfile(risk_level=3, capital=Decimal("1000"), max_drawdown=0.2)
    sup = BotSupervisor(
        InMemoryBotRepository([BotConfig(BOT, profile), BotConfig("dev-bot", profile)]),
        MemorySink(),
    )
    sup.sync()
    book = sup.runners[BOT].book
    book.peak_equity = Decimal("1250")
    now = [0.0]
    rep = EquitySnapshotReporter(api.client(), sup, interval=300, clock=lambda: now[0])
    at = datetime(2026, 11, 22, 10, 7, 31, tzinfo=UTC)
    assert rep.maybe_report(at) == 1  # el bot sin UUID no tiene fila en D1
    assert rep.maybe_report(at) == 0
    (req,) = api.calls("POST", "/internal/equity-snapshots")
    import json

    (snap,) = json.loads(req.content)["snapshots"]
    assert snap["bot_id"] == BOT and snap["ts"] == "2026-11-22T10:05:00+00:00"
    assert snap["equity"] == 1000.0 and snap["cash"] == 1000.0
    assert snap["positions_value"] == 0.0 and snap["drawdown"] == 0.2
    api.fail[("POST", "/internal/equity-snapshots")] = [httpx.Response(400, json={})]
    now[0] = 400
    assert rep.maybe_report(at) == 0 and rep.failed == 1
    assert rep.sent == 1


def test_decision_sink_omits_non_uuid_bot(make_bot, make_event):
    api = FakeWorkerApi()
    runner, _memory, _ = make_bot("bot-dev")
    runner.sink = WorkerApiDecisionSink(api.client(), kind="shadow")
    (envelope,) = runner.on_event(make_event(1.0))
    (row,) = api.decisions
    assert "bot_id" not in row and row["kind"] == "shadow"
    assert row["payload"]["bot_id"] == envelope.bot_id == "bot-dev"
