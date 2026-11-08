"""Streams de Redis, BotSupervisor, OrchestratorWorker y exposición por nivel (#19)."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import fakeredis
import httpx
import numpy as np
import pytest
from tm_agents import __main__ as worker_main
from tm_agents.montecarlo import ONE_YEAR_DAYS, bootstrap
from tm_agents.orchestrator.bot import BotCommand, BotConfig, BotProfile, BotStatus, CommandType
from tm_agents.orchestrator.exposure import (
    CONSERVATIVE_DD_FACTOR,
    TREND_SENSITIVITY,
    TREND_VOL_SENSITIVITY,
    exposure_for_level,
    level_drawdown,
    measure_sensitivity,
    sensitivity_for,
)
from tm_agents.orchestrator.runtime import OrchestratorWorker, memory_sinks
from tm_agents.orchestrator.streams import (
    COMMANDS_STREAM,
    DECISIONS_STREAM,
    MemorySink,
    RedisStreamSink,
    StreamReader,
)
from tm_agents.orchestrator.supervisor import (
    BotSupervisor,
    CommandListener,
    InMemoryBotRepository,
    PostgRESTBotRepository,
    bot_from_row,
)
from tm_agents.registry import StrategyRecord
from tm_agents.risk import RISK_TABLE
from tm_agents.signals import (
    SIGNALS_STREAM,
    ListSignalSource,
    RedisSignalSink,
    RedisSignalSource,
    StrategySnapshot,
)
from tm_agents.strategy_stats import StrategyState
from tm_shared.contracts import DecisionEnvelope

NOW = datetime(2024, 3, 1, 0, 0, 10, tzinfo=UTC)


def config(bot_id: str, level: int = 3, status: BotStatus = BotStatus.RUNNING) -> BotConfig:
    profile = BotProfile(risk_level=level, capital=Decimal("10000"), max_drawdown=0.4)
    return BotConfig(bot_id, profile, status=status)


# --- streams -------------------------------------------------------------------------------
def test_signals_round_trip_through_redis(make_event):
    client = fakeredis.FakeRedis()
    client.xadd(SIGNALS_STREAM, {"data": "viejo"})  # antes de arrancar: no se lee
    source = RedisSignalSource(client)
    sink = RedisSignalSink(client)
    ev = make_event(1.0)
    assert "-" in sink.publish(ev)
    client.xadd(SIGNALS_STREAM, {"data": "{no es json"})
    assert source.poll() == [ev]
    assert source.invalid == 1
    assert source.poll() == []


def test_stream_reader_on_missing_stream_starts_at_zero():
    client = fakeredis.FakeRedis()
    reader = StreamReader(client, "tm:nuevo")
    assert reader.last_id == "0-0"
    client.xadd("tm:nuevo", {"a": "1"})
    assert [f for _, f in reader.read()] == [{b"a": b"1"}]


def test_decision_envelopes_are_readable_by_the_engine_format(make_bot, make_event):
    client = fakeredis.FakeRedis()
    runner, _, _ = make_bot()
    runner.sink = RedisStreamSink(client, DECISIONS_STREAM)
    (envelope,) = runner.on_event(make_event(1.0))
    ((_, entries),) = client.xread({DECISIONS_STREAM: "0-0"})
    raw = entries[0][1][b"data"]
    parsed = DecisionEnvelope.model_validate_json(raw)
    assert parsed == envelope and parsed.bot_id == "bot-a" and parsed.sleeve == "trend"


def test_list_signal_source_releases_in_order(make_event):
    late, early = make_event(1.0, day=2), make_event(0.5, day=1)
    source = ListSignalSource([late, early])
    assert source.poll(early.bar_close) == [early]
    assert source.poll() == [late]
    assert source.poll() == []


# --- supervisor ----------------------------------------------------------------------------
def test_supervisor_syncs_runners_and_fans_out_events(make_event):
    repo = InMemoryBotRepository(
        [config("a", 1), config("b", 5), config("c", 3, BotStatus.STOPPED)]
    )
    sink, shadow = memory_sinks()
    sup = BotSupervisor(repo, sink, shadow_sink=shadow)
    sup.sync()
    assert sorted(sup.runners) == ["a", "b"]
    out = sup.on_event(make_event(1.0))
    assert [d.bot_id for d in out] == ["a", "b"]
    assert out[0].decision.quantity < out[1].decision.quantity

    repo.bots["a"] = config("a", 1, BotStatus.STOPPED)
    sup.sync()
    assert sorted(sup.runners) == ["b"]


def test_supervisor_commands(make_event):
    repo = InMemoryBotRepository([config("a"), config("z", status=BotStatus.STOPPED)])
    sup = BotSupervisor(repo, MemorySink())
    sup.sync()
    sup.on_event(make_event(1.0))
    closes = sup.handle_command(BotCommand(CommandType.KILL, "a", "pánico"), NOW)
    assert len(closes) == 1 and closes[0].target_quantity == 0
    assert "a" not in sup.runners
    assert sup.handle_command(BotCommand(CommandType.STOP, "a"), NOW) == []
    assert sup.handle_command(BotCommand(CommandType.START, "nadie"), NOW) == []
    sup.handle_command(BotCommand(CommandType.START, "z"), NOW)
    assert sup.runners["z"].running


def test_supervisor_survives_repository_and_runner_errors(make_event):
    class Broken:
        def running(self):
            raise httpx.ConnectError("supabase caído")

        def get(self, bot_id):
            return None

    sup = BotSupervisor(Broken(), MemorySink())
    sup.runners["x"] = object()  # un runner roto no tumba el reparto
    sup.sync()
    assert "x" in sup.runners
    assert sup.on_event(make_event(1.0)) == []


def test_postgrest_repository_reads_running_bots():
    row = {
        "id": "7f0c",
        "name": "Bot de Ana",
        "status": "running",
        "strategy_config": {"kronos": {"enabled": False}},
        "profile": {
            "risk_level": 2,
            "capital": "750.5",
            "max_drawdown": 0.15,
            "horizon": "long",
            "instruments": ["BTCUSDT"],
        },
    }
    bad = {"id": "roto", "status": "running", "profile": {"risk_level": 9}}
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[row, bad])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    repo = PostgRESTBotRepository("http://supabase", "eyJ.key", client=client)
    (bot,) = repo.running()
    assert bot.bot_id == "7f0c" and bot.profile.capital == Decimal("750.5")
    assert bot.profile.instruments == ("BTCUSDT",) and not bot.enabled("kronos")
    assert seen[0].url.params["status"] == "eq.running"
    assert seen[0].headers["Authorization"] == "Bearer eyJ.key"
    assert repo.get("7f0c").bot_id == "7f0c"
    assert "eq.7f0c" in str(seen[-1].url)
    with pytest.raises(ValueError):
        PostgRESTBotRepository.from_env({})
    assert bot_from_row({**row, "strategy_config": None}).strategy_config == {}


def test_command_listener_parses_and_skips_invalid():
    client = fakeredis.FakeRedis()
    listener = CommandListener(client)
    client.xadd(COMMANDS_STREAM, {"type": "kill", "bot_id": "a", "reason": "x"})
    client.xadd(COMMANDS_STREAM, {"type": "explode", "bot_id": "a"})
    client.xadd(COMMANDS_STREAM, {"bot_id": "a"})
    (cmd,) = listener.poll()
    assert cmd.type is CommandType.KILL and cmd.bot_id == "a" and cmd.id


# --- worker --------------------------------------------------------------------------------
class FakeShared:
    def __init__(self):
        self.closes: list[datetime] = []

    def on_bar_close(self, now):
        self.closes.append(now)
        return []


def test_worker_tick_runs_both_layers(make_event):
    clock = {"now": NOW}
    shared = FakeShared()
    client = fakeredis.FakeRedis()
    sup = BotSupervisor(InMemoryBotRepository([config("a")]), MemorySink())
    signals = ListSignalSource([make_event(1.0)])
    commands = CommandListener(client)
    w = OrchestratorWorker(
        shared, sup, signals, commands, clock=lambda: clock["now"], max_catch_up=3
    )
    stats = w.tick()
    assert stats["events_consumed"] == 1 and stats["decisions"] == 1
    assert shared.closes == [datetime(2024, 3, 1, tzinfo=UTC)]
    assert w.tick()["events_consumed"] == 0  # el mismo minuto no se repite
    assert len(shared.closes) == 1

    clock["now"] = NOW + timedelta(minutes=10)
    client.xadd(COMMANDS_STREAM, {"type": "kill", "bot_id": "a"})
    stats = w.tick()
    assert len(shared.closes) == 4  # recupera como máximo 3 minutos
    assert stats["commands"] == 1 and stats["decisions"] == 1


def test_build_worker_without_redis_returns_none():
    assert worker_main.build_worker({}) is None


def test_build_worker_with_fake_redis(monkeypatch):
    import redis

    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(lambda url: fakeredis.FakeRedis()))
    w = worker_main.build_worker(
        {"REDIS_URL": "redis://x", "ORCHESTRATOR_SYMBOLS": "btcusdt, ", "TM_DATA_DIR": "/tmp/x"}
    )
    assert isinstance(w, OrchestratorWorker)
    assert w.shared.symbols == ("BTCUSDT",)
    assert w.commands is not None
    w.tick()

    only_shared = worker_main.build_worker(
        {"REDIS_URL": "redis://x", "ORCHESTRATOR_BOTS": "0", "ORCHESTRATOR_SHARED": "off"}
    )
    assert only_shared.shared is None and only_shared.commands is None
    assert only_shared.signals.poll() == []
    assert only_shared.supervisor.sink.publish(object()) is None


def test_main_loop_ticks_worker(tmp_path, monkeypatch):
    ticks = []

    class FakeWorker:
        def tick(self):
            ticks.append(1)
            if len(ticks) == 2:
                raise RuntimeError("falla de un ciclo")

    monkeypatch.setattr(worker_main, "HEARTBEAT", tmp_path / "hb")
    monkeypatch.setattr(worker_main, "INTERVAL", 0.01)
    monkeypatch.setattr(worker_main, "TICK", 0.01)
    monkeypatch.setattr(worker_main, "build_worker", lambda: FakeWorker())
    handlers = {}
    monkeypatch.setattr(worker_main.signal, "signal", lambda sig, fn: handlers.__setitem__(sig, fn))
    t = threading.Thread(target=worker_main.main, args=([],), daemon=True)
    t.start()
    while len(ticks) < 3:
        pass
    handlers[worker_main.signal.SIGTERM]()
    t.join(timeout=2)
    assert not t.is_alive() and (tmp_path / "hb").exists()


def test_main_loop_survives_build_errors(tmp_path, monkeypatch):
    def boom():
        raise RuntimeError("sin redis")

    monkeypatch.setattr(worker_main, "HEARTBEAT", tmp_path / "hb")
    monkeypatch.setattr(worker_main, "INTERVAL", 0.01)
    monkeypatch.setattr(worker_main, "build_worker", boom)
    handlers = {}
    monkeypatch.setattr(worker_main.signal, "signal", lambda sig, fn: handlers.__setitem__(sig, fn))
    t = threading.Thread(target=worker_main.main, args=([],), daemon=True)
    t.start()
    while not (tmp_path / "hb").exists():
        pass
    handlers[worker_main.signal.SIGINT]()
    t.join(timeout=2)
    assert not t.is_alive()


# --- exposición por nivel ------------------------------------------------------------------
def test_exposure_uses_the_measured_curve_not_the_proportional_rule():
    # Nivel 3 (20 %) con el p95 a 1 año de 43.8 % del Trend: la regla proporcional daría
    # 45.6 %, pero con 45.6 % invertido el p95 medido es de ~22.2 %; la curva pide ~40.6 %.
    k = exposure_for_level(3, 0.4383, TREND_SENSITIVITY)
    assert k == pytest.approx(0.406, abs=0.002)
    assert k < 0.20 / 0.4383
    levels = [exposure_for_level(n, 0.4383, TREND_SENSITIVITY) for n in range(1, 6)]
    assert levels == sorted(levels) and levels[-1] < 0.40 / 0.4383


def test_exposure_without_curve_is_conservative():
    k = exposure_for_level(3, 0.4383)
    assert k == pytest.approx(0.20 / 0.4383 / CONSERVATIVE_DD_FACTOR)
    for curve in (TREND_SENSITIVITY, TREND_VOL_SENSITIVITY):
        for level in range(1, 5):
            assert exposure_for_level(level, curve[0][1]) <= exposure_for_level(
                level, curve[0][1], curve
            )


def test_measure_sensitivity_reproduces_the_lab_method():
    rng = np.random.default_rng(0)
    returns = rng.normal(0.001, 0.03, size=730)
    curve = measure_sensitivity(returns, (1.0, 0.5), n_simulations=500)
    (k1, p1), (k2, p2) = curve
    assert (k1, k2) == (1.0, 0.5)
    assert 0 < p2 < p1 and p2 > 0.5 * p1 * 0.95  # baja menos que en proporción
    same = bootstrap(returns, 500, seed=42, block_size=20, horizon=ONE_YEAR_DAYS)
    assert p1 == pytest.approx(same.max_drawdown.p95)


@pytest.mark.parametrize("p95", [None, 0.0, float("nan")])
def test_exposure_without_p95_is_zero(p95):
    assert exposure_for_level(5, p95) == 0.0


def test_exposure_edges():
    assert exposure_for_level(5, 0.30) == 1.0  # el nivel tolera el p95 completo
    fallback = 0.1 / 0.5 / CONSERVATIVE_DD_FACTOR
    # curva sin el punto de exposición completa: no sirve, se usa el factor conservador
    assert exposure_for_level(1, 0.5, [(0.5, 0.3), (0.2, 0.1)]) == pytest.approx(fallback)
    # curva válida: p95 relativo de 0.8 con la mitad invertida
    assert exposure_for_level(1, 0.5, [(1.0, 0.5), (0.5, 0.4)]) == pytest.approx(0.125)
    assert exposure_for_level(1, 0.5, [(1.0, 0.5), (0.5, 0.6)]) == pytest.approx(fallback)
    assert sensitivity_for("trend", None) is TREND_SENSITIVITY
    assert sensitivity_for("kronos", None) is None
    assert sensitivity_for("trend", [(1.0, 0.4)]) == [(1.0, 0.4)]
    with pytest.raises(ValueError):
        level_drawdown(6)
    with pytest.raises(ValueError):
        level_drawdown("alto")  # type: ignore[arg-type]
    # el drawdown de cada nivel sale de la tabla única del RiskAgent (#86)
    assert {lvl: level_drawdown(lvl) for lvl in RISK_TABLE} == {
        lvl: float(spec.max_drawdown) for lvl, spec in RISK_TABLE.items()
    }


def test_snapshot_from_record_reads_the_lab_report():
    report = {
        "aggregate": {"volatility": 0.4, "sharpe": "nan"},
        "montecarlo": {"max_drawdown": {"p95": 0.5}},
        "horizon": "long",
        "exposure_sensitivity": [[1.0, 0.5], [0.5, 0.3]],
    }
    snap = StrategySnapshot.from_record(
        StrategyRecord("x", state=StrategyState.SHADOW, lab_report=report)
    )
    assert snap.volatility == 0.4 and snap.oos_sharpe == 0.0
    assert snap.sensitivity == ((1.0, 0.5), (0.5, 0.3))
    stats = snap.to_stats(state=StrategyState.ACTIVE)
    assert stats.state is StrategyState.ACTIVE and stats.max_drawdown_p95 == 0.5
    vol = StrategySnapshot.from_record(
        StrategyRecord(
            "trend",
            lab_report={
                **report,
                "exposure_sensitivity": None,
                "config": {"strategy": "trend_vol"},
            },
        )
    )
    assert vol.sensitivity == TREND_VOL_SENSITIVITY
    empty = StrategySnapshot.from_record(StrategyRecord("y", state=StrategyState.ACTIVE))
    assert empty.volatility == 0.0 and empty.max_drawdown_p95 is None
    assert json.loads(snap.model_dump_json())["state"] == "shadow"
