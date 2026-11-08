"""ReferenceService (#57): ledger por estrategia, LifecycleMonitor cableado y shadow sin órdenes.

Sin red: registro en memoria o PostgREST simulado con ``httpx.MockTransport``, y Redis con
fakeredis.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs
from uuid import uuid4

import fakeredis
import httpx
import pytest
from tm_agents.lifecycle import LifecycleMonitor
from tm_agents.reference import (
    REFERENCE_CHECKPOINT,
    InMemoryReferenceStore,
    PostgRESTReferenceStore,
    ReferenceService,
    ReferenceStoreError,
    TradingSleeveGuard,
    can_send_orders,
)
from tm_agents.reference.service import DrawdownBand
from tm_agents.registry import (
    PostgRESTBackend,
    RegistryError,
    StaleStateError,
    StrategyRegistry,
)
from tm_agents.signals import ListSignalSource, RedisSignalSource, SignalEvent, publish_signal
from tm_agents.strategy_stats import StrategyState as S
from tm_shared.contracts import Direction, Horizon, Signal, Timeframe

HUMAN = "11111111-1111-1111-1111-111111111111"
#: p95 bajo (5 %) y p99 alto (50 %): una caída de 10 % degrada pero no retira de inmediato.
LAB = {"montecarlo": {"max_drawdown": {"p5": 0.02, "p50": 0.03, "p95": 0.05, "p99": 0.5}}}
PATHS = {
    S.SHADOW: [S.LAB, S.SHADOW],
    S.ACTIVE: [S.LAB, S.SHADOW, S.ACTIVE],
    S.PROBATION: [S.LAB, S.SHADOW, S.ACTIVE, S.PROBATION],
}


@pytest.fixture
def base() -> datetime:
    # Después de "ahora": el historial del registro en memoria usa el reloj real.
    return datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=1)


def add_strategy(
    registry: StrategyRegistry,
    name: str,
    state: S,
    entered_at: datetime,
    lab_report: dict[str, Any] | None = LAB,
) -> None:
    registry.register(name)
    for to in PATHS[state]:
        registry.transition(name, to, HUMAN if to is S.ACTIVE else "system", "prueba")
    registry.backend.update(name, {"state_changed_at": entered_at, "lab_report": lab_report})


def sig(
    strategy: str,
    at: datetime,
    close: float = 100.0,
    direction: Direction = Direction.LONG,
    *,
    symbol: str = "BTCUSDT",
    timeframe: Timeframe = Timeframe.H4,
) -> SignalEvent:
    signal = Signal(
        symbol=symbol,
        direction=direction,
        confidence=1.0 if direction is Direction.LONG else 0.0,
        horizon=Horizon.SHORT,
        timeframe=timeframe,
        source=strategy,
        generated_at=at,
    )
    return SignalEvent.from_signal(signal, close)


def make_service(registry=None, store=None, source=None, **kwargs) -> ReferenceService:
    kwargs.setdefault("symbols", ("BTCUSDT",))
    return ReferenceService(
        StrategyRegistry() if registry is None else registry,
        InMemoryReferenceStore() if store is None else store,
        ListSignalSource() if source is None else source,
        **kwargs,
    )


# ---------------------------------------------------------------------------------------
# Shadow: corre en vivo, guarda trades, equity y métricas
# ---------------------------------------------------------------------------------------
def test_shadow_ledger_records_trades_equity_and_metrics(base):
    registry = StrategyRegistry()
    add_strategy(registry, "kronos", S.SHADOW, base)
    store = InMemoryReferenceStore()
    source = ListSignalSource()
    svc = make_service(registry, store, source)
    source.push(sig("kronos", base + timedelta(hours=4), 100.0))
    source.push(sig("kronos", base + timedelta(hours=8), 110.0, Direction.FLAT))
    assert svc.run_once(base + timedelta(hours=9)) == 2

    rec = registry.get("kronos")
    sid = rec.id
    (trade,) = store.trades_for(sid)
    assert trade["state"] == "shadow" and trade["symbol"] == "BTCUSDT"
    assert trade["ret"] == pytest.approx((110 * 0.999 - 100 * 1.001) / (100 * 1.001))
    # Un punto por día UTC: si base + 9 h cruza la medianoche hay dos; vale el último.
    point = store.equity_for(sid)[-1]
    assert point["equity"] == pytest.approx(100_000 * (1 + trade["ret"]))
    m = rec.live_metrics
    assert m["source"] == "reference_ledger" and m["state"] == "shadow"
    assert m["trades"] == 1 and m["capital"] == 100_000
    assert m["lab"]["max_drawdown_p95"] == 0.05
    assert m["shadow"]["failures"] == 0
    assert m["shadow"]["days_clean"] == pytest.approx(9 / 24)
    assert m["shadow"]["eligible_for_promotion"] is False
    assert m["ledger"]["started_at"] == base.isoformat()
    assert rec.state is S.SHADOW  # el monitor nunca mueve una estrategia en shadow
    assert svc.transitions == []


def test_monitor_runs_on_each_trade_close_and_daily(base, monkeypatch):
    registry = StrategyRegistry()
    add_strategy(registry, "kronos", S.SHADOW, base)
    source = ListSignalSource()
    monitor = LifecycleMonitor()
    calls = []
    real = monitor.check
    monkeypatch.setattr(monitor, "check", lambda *a: calls.append(a) or real(*a))
    svc = make_service(registry, source=source, monitor=monitor)
    for i in range(3):
        source.push(sig("kronos", base + timedelta(hours=8 * i + 1), 100.0))
        source.push(sig("kronos", base + timedelta(hours=8 * i + 5), 101.0, Direction.FLAT))
    svc.run_once(base + timedelta(days=1))
    # 3 trades cerrados + 1 revisión diaria
    assert len(calls) == 4
    state, stats, band = calls[0]
    assert state is S.SHADOW and len(stats.recent_trades) == 1
    assert band.max_drawdown.p95 == 0.05
    svc.run_once(base + timedelta(days=1, hours=1))  # mismo día: sin revisión diaria
    assert len(calls) == 4
    svc.run_once(base + timedelta(days=2))
    assert len(calls) == 5


# ---------------------------------------------------------------------------------------
# LifecycleMonitor contra el ledger de referencia
# ---------------------------------------------------------------------------------------
def test_active_drawdown_beyond_p95_moves_to_probation_on_trade_close(base, caplog):
    registry = StrategyRegistry()
    add_strategy(registry, "trend", S.ACTIVE, base)
    svc = make_service(registry, sizing={"trend": "full"})
    svc.sync(base)
    svc.handle(sig("trend", base + timedelta(hours=1), 100.0, timeframe=Timeframe.D1), base)
    with caplog.at_level(logging.WARNING, logger="tm_agents.reference.service"):
        svc.handle(sig("trend", base + timedelta(hours=2), 90.0, Direction.FLAT), base)
    rec = registry.get("trend")
    assert rec.state is S.PROBATION
    last = registry.history("trend")[-1]
    assert (last.from_state, last.to_state, last.actor) == (S.ACTIVE, S.PROBATION, "system")
    assert "p95" in last.reason
    assert any("LifecycleMonitor: 'trend' active -> probation" in r.message for r in caplog.records)
    (name, transition) = svc.transitions[0]
    assert name == "trend" and transition.to_state is S.PROBATION
    assert svc.tracked["trend"].record.state is S.PROBATION


def test_probation_without_recovery_is_retired_by_daily_check(base):
    registry = StrategyRegistry()
    add_strategy(registry, "kronos", S.PROBATION, base - timedelta(days=31))
    source = ListSignalSource()
    store = InMemoryReferenceStore()
    svc = make_service(registry, store, source)
    source.push(sig("kronos", base + timedelta(hours=1), 100.0))
    source.push(sig("kronos", base + timedelta(hours=5), 90.0))  # sigue comprado, -10 %
    svc.run_once(base + timedelta(hours=6))
    rec = registry.get("kronos")
    assert rec.state is S.RETIRED
    assert "observación" in registry.history("kronos")[-1].reason
    # las métricas finales quedan guardadas y luego se deja de seguir
    assert rec.live_metrics["drawdown"] == pytest.approx(0.1, rel=2e-2)
    assert store.equity_for(rec.id)[0]["state"] == "probation"
    svc.run_once(base + timedelta(hours=7))
    assert "kronos" not in svc.tracked
    assert svc.handle(sig("kronos", base + timedelta(hours=8)), base) == 0


def test_missing_lab_band_never_transitions(base, caplog):
    registry = StrategyRegistry()
    add_strategy(registry, "trend", S.ACTIVE, base, lab_report=None)
    svc = make_service(registry, sizing={"trend": "full"})
    svc.sync(base)
    svc.handle(sig("trend", base + timedelta(hours=1), 100.0), base)
    with caplog.at_level(logging.WARNING):
        svc.handle(sig("trend", base + timedelta(hours=2), 50.0, Direction.FLAT), base)
    assert registry.get("trend").state is S.ACTIVE
    assert "no tiene la distribución" in caplog.text
    assert svc.live_metrics(svc.tracked["trend"], base)["lab"] is None


def test_failed_transition_is_logged_and_state_refreshed(base, monkeypatch, caplog):
    registry = StrategyRegistry()
    add_strategy(registry, "trend", S.ACTIVE, base)
    svc = make_service(registry, sizing={"trend": "full"})
    svc.sync(base)

    def boom(*_a, **_k):
        raise StaleStateError("otra escritura llegó antes")

    monkeypatch.setattr(registry, "transition", boom)
    svc.handle(sig("trend", base + timedelta(hours=1), 100.0), base)
    with caplog.at_level(logging.WARNING):
        svc.handle(sig("trend", base + timedelta(hours=2), 80.0, Direction.FLAT), base)
    assert "no se pudo aplicar active -> probation" in caplog.text
    assert svc.transitions == []


# ---------------------------------------------------------------------------------------
# Shadow nunca envía órdenes
# ---------------------------------------------------------------------------------------
def test_shadow_strategies_never_send_orders(base):
    """El ledger de shadow solo lee ``tm:signals``: nunca escribe ``tm:decisions``."""
    r = fakeredis.FakeRedis()
    registry = StrategyRegistry()
    add_strategy(registry, "kronos", S.SHADOW, base)
    add_strategy(registry, "trend", S.ACTIVE, base)
    source = RedisSignalSource(r, checkpoint_key=REFERENCE_CHECKPOINT)
    svc = make_service(registry, source=source)
    for i, name in enumerate(["kronos", "trend", "kronos", "trend"]):
        direction = Direction.LONG if i < 2 else Direction.FLAT
        publish_signal(r, sig(name, base + timedelta(hours=i + 1), 100.0 + i, direction))
    assert svc.run_once(base + timedelta(hours=6)) == 4
    assert svc.tracked["kronos"].ledger.n_trades == 1
    keys = {k.decode() for k in r.keys("*")}
    assert keys == {"tm:signals", "tm:reference:last_signal_id"}
    assert not r.exists("tm:decisions")
    # y el guardia del motor rechaza cualquier decisión de un sleeve en shadow
    guard = TradingSleeveGuard(registry)
    assert guard.allows("kronos") is False
    assert guard.allows("trend") is True
    assert "shadow" in guard.reason("kronos")


def test_guard_fails_closed_and_caches():
    registry = StrategyRegistry()
    registry.register("trend")
    clock = [0.0]
    guard = TradingSleeveGuard(registry, ttl=10, clock=lambda: clock[0])
    assert guard("trend") is False  # research
    assert guard("nope") is False
    assert "no tiene estrategia" in guard.reason("nope")
    for to in PATHS[S.ACTIVE]:
        registry.transition("trend", to, HUMAN if to is S.ACTIVE else "system", "x")
    assert guard("trend") is False  # todavía en caché
    clock[0] = 11
    assert guard("trend") is True

    class Broken:
        def find(self, name):
            raise RegistryError("sin red")

    assert TradingSleeveGuard(Broken()).allows("trend") is False  # type: ignore[arg-type]
    # #89: los cierres pasan en cualquier estado, aunque el registro falle
    assert TradingSleeveGuard(Broken()).allows("trend", reduce_only=True) is True  # type: ignore[arg-type]
    assert guard("nope", reduce_only=True) is True
    registry.transition("trend", S.RETIRED, "system", "x")
    clock[0] = 30
    assert guard("trend") is False and guard("trend", reduce_only=True) is True
    assert [can_send_orders(s) for s in S] == [s in (S.ACTIVE, S.PROBATION) for s in S]
    assert can_send_orders(None) is False


# ---------------------------------------------------------------------------------------
# Días en shadow sin fallas
# ---------------------------------------------------------------------------------------
def test_failures_reset_clean_days_and_promotion_eligibility(base):
    registry = StrategyRegistry()
    add_strategy(registry, "kronos", S.SHADOW, base)
    source = ListSignalSource()
    svc = make_service(registry, source=source)
    source.push(sig("kronos", base + timedelta(hours=1), symbol="SOLUSDT"))  # no la opera: falla
    svc.run_once(base + timedelta(days=1))
    shadow = registry.get("kronos").live_metrics["shadow"]
    assert shadow["failures"] == 1
    assert "SOLUSDT" in shadow["last_failure_reason"]
    assert shadow["clean_since"] == (base + timedelta(days=1)).isoformat()
    assert shadow["days_clean"] == 0
    # 8 días después, con señales al día, ya cumple los 7 días mínimos
    for h in range(4, 9 * 24, 4):
        source.push(sig("kronos", base + timedelta(hours=h), 100.0))
    svc.run_once(base + timedelta(days=9))
    shadow = registry.get("kronos").live_metrics["shadow"]
    assert shadow["failures"] == 1
    assert shadow["days_clean"] == pytest.approx(8)
    assert shadow["eligible_for_promotion"] is True


def test_silence_counts_as_one_failure(base):
    registry = StrategyRegistry()
    add_strategy(registry, "kronos", S.SHADOW, base)
    svc = make_service(registry)
    svc.sync(base)
    svc.handle(sig("kronos", base + timedelta(hours=1), 100.0), base)
    svc.daily(base + timedelta(hours=10))  # 9 h < 3 velas de 4 h
    assert svc.tracked["kronos"].health.failures == 0
    svc.daily(base + timedelta(hours=14))
    svc.daily(base + timedelta(hours=20))  # el mismo silencio no se cuenta dos veces
    health = svc.tracked["kronos"].health
    assert health.failures == 1 and "sin señales" in health.last_failure_reason


def test_never_signalled_counts_as_failure_after_grace(base):
    registry = StrategyRegistry()
    add_strategy(registry, "kronos", S.SHADOW, base)
    svc = make_service(registry)
    svc.sync(base)
    svc.daily(base + timedelta(days=1))
    assert svc.tracked["kronos"].health.failures == 0
    svc.daily(base + timedelta(days=3))
    svc.daily(base + timedelta(days=4))
    assert svc.tracked["kronos"].health.failures == 1


def test_unexpected_error_in_one_strategy_is_a_failure(base, monkeypatch):
    registry = StrategyRegistry()
    add_strategy(registry, "kronos", S.SHADOW, base)
    svc = make_service(registry)
    svc.sync(base)

    def boom(_env):
        raise RuntimeError("bug")

    monkeypatch.setattr(svc.tracked["kronos"].ledger, "on_signal", boom)
    assert svc.handle(sig("kronos", base + timedelta(hours=1)), base) == 0
    assert "error inesperado" in svc.tracked["kronos"].health.last_failure_reason


# ---------------------------------------------------------------------------------------
# Sincronización, reinicios y errores
# ---------------------------------------------------------------------------------------
def test_ignores_untracked_and_old_signals(base):
    registry = StrategyRegistry()
    add_strategy(registry, "kronos", S.SHADOW, base)
    registry.register("intraday_momentum")
    svc = make_service(registry)
    svc.sync(base)
    assert set(svc.tracked) == {"kronos"}
    assert svc.handle(sig("intraday_momentum", base + timedelta(hours=1)), base) == 0
    assert svc.handle(sig("kronos", base - timedelta(hours=1)), base) == 0
    assert svc.ignored == 2


def test_reentering_shadow_starts_a_new_run(base):
    registry = StrategyRegistry()
    add_strategy(registry, "kronos", S.SHADOW, base)
    svc = make_service(registry)
    svc.sync(base)
    svc.handle(sig("kronos", base + timedelta(hours=1), 100.0), base)
    later = base + timedelta(days=10)
    registry.backend.update("kronos", {"state_changed_at": later})
    svc.sync(later)
    ledger = svc.tracked["kronos"].ledger
    assert ledger.started_at == later and ledger.positions_value == 0


def test_restart_resumes_ledger_from_live_metrics(base):
    registry = StrategyRegistry()
    add_strategy(registry, "kronos", S.SHADOW, base)
    source = ListSignalSource([sig("kronos", base + timedelta(hours=1), symbol="SOLUSDT")])
    svc = make_service(registry, source=source)
    source.push(sig("kronos", base + timedelta(hours=2), 100.0))
    source.push(sig("kronos", base + timedelta(hours=6), 120.0))
    svc.run_once(base + timedelta(hours=7))
    before = svc.tracked["kronos"]

    again = make_service(registry)
    again.sync(base + timedelta(hours=8))
    after = again.tracked["kronos"]
    assert after.ledger.equity == pytest.approx(before.ledger.equity)
    assert after.ledger.peak_equity == pytest.approx(before.ledger.peak_equity)
    assert after.health.failures == 1
    assert after.dirty is False


def test_unreadable_or_stale_saved_state_starts_fresh(base):
    registry = StrategyRegistry()
    add_strategy(registry, "kronos", S.SHADOW, base)
    registry.record_live_metrics("kronos", {"ledger": {"books": "basura"}})
    svc = make_service(registry)
    svc.sync(base)
    assert svc.tracked["kronos"].ledger.started_at == base
    old = make_service(registry)
    old.sync(base)
    state = old.tracked["kronos"].ledger.to_state()
    state["started_at"] = (base - timedelta(days=30)).isoformat()
    registry.record_live_metrics("kronos", {"ledger": state})
    fresh = make_service(registry)
    fresh.sync(base)
    assert fresh.tracked["kronos"].ledger.started_at == base


def test_active_without_saved_ledger_starts_at_last_shadow_entry(base, monkeypatch):
    registry = StrategyRegistry()
    add_strategy(registry, "trend", S.ACTIVE, base)
    entry = [t.created_at for t in registry.history("trend") if t.to_state is S.SHADOW][-1]
    svc = make_service(registry)
    svc.sync(base)
    assert svc.tracked["trend"].ledger.started_at == entry
    # sin historial legible, empieza en la entrada al estado actual
    monkeypatch.setattr(registry, "history", lambda _n: (_ for _ in ()).throw(RegistryError("x")))
    other = make_service(registry)
    other.sync(base)
    assert other.tracked["trend"].ledger.started_at == base


class FlakyStore(InMemoryReferenceStore):
    def __init__(self) -> None:
        super().__init__()
        self.down = True

    def save_trades(self, rows):
        if self.down:
            raise ReferenceStoreError("sin base")
        super().save_trades(rows)

    def upsert_equity(self, points):
        if self.down:
            raise ReferenceStoreError("sin base")
        super().upsert_equity(points)


class CountingSource(ListSignalSource):
    commits = 0

    def commit(self) -> None:
        self.commits += 1


def test_store_errors_keep_pending_rows_and_delay_commit(base):
    registry = StrategyRegistry()
    add_strategy(registry, "kronos", S.SHADOW, base)
    store = FlakyStore()
    source = CountingSource()
    svc = make_service(registry, store, source)
    source.push(sig("kronos", base + timedelta(hours=1), 100.0))
    source.push(sig("kronos", base + timedelta(hours=2), 101.0, Direction.FLAT))
    svc.run_once(base + timedelta(hours=3))
    tracked = svc.tracked["kronos"]
    assert tracked.dirty and len(tracked.pending_trades) == 1
    assert source.commits == 0
    store.down = False
    svc.run_once(base + timedelta(hours=4))
    assert not tracked.dirty and tracked.pending_trades == []
    assert len(store.trades_for(registry.get("kronos").id)) == 1
    assert source.commits == 1


def test_equity_error_and_registry_errors_are_retried(base, monkeypatch):
    registry = StrategyRegistry()
    add_strategy(registry, "kronos", S.SHADOW, base)
    store = FlakyStore()
    svc = make_service(registry, store)
    svc.sync(base)
    svc.daily(base + timedelta(hours=1))
    svc.flush(base + timedelta(hours=1))  # sin trades pendientes, falla la equity
    assert svc.tracked["kronos"].dirty
    store.down = False

    def no_registry(*_a, **_k):
        raise RegistryError("sin red")

    monkeypatch.setattr(registry, "record_live_metrics", no_registry)
    svc.flush(base + timedelta(hours=2))
    assert svc.tracked["kronos"].dirty
    monkeypatch.setattr(registry, "list", no_registry)
    svc.sync(base)  # no tumba el ciclo
    assert "kronos" in svc.tracked
    monkeypatch.setattr(registry, "get", no_registry)
    svc._refresh(svc.tracked["kronos"])  # tampoco


def test_equity_points_for_past_days_and_constructor_validation(base):
    registry = StrategyRegistry()
    add_strategy(registry, "kronos", S.SHADOW, base)
    store = InMemoryReferenceStore()
    source = ListSignalSource()
    svc = make_service(registry, store, source)
    source.push(sig("kronos", base + timedelta(hours=1), 100.0))
    source.push(sig("kronos", base + timedelta(days=1, hours=1), 90.0))
    svc.run_once(base + timedelta(days=1, hours=2))
    rows = store.equity_for(registry.get("kronos").id)
    assert len(rows) == 2
    assert rows[0]["positions_value"] == pytest.approx(rows[0]["equity"])
    assert rows[0]["cash"] == pytest.approx(0, abs=1e-6)
    assert rows[1]["drawdown"] == pytest.approx(0.1, rel=2e-2)
    with pytest.raises(ValueError):
        make_service(silence_bars=0)


# ---------------------------------------------------------------------------------------
# Banda del laboratorio
# ---------------------------------------------------------------------------------------
def test_drawdown_band_formats():
    wf = DrawdownBand.from_lab_report({**LAB, "aggregate": {"sharpe": 0.9}})
    assert wf.max_drawdown.p95 == 0.05 and wf.max_drawdown.p99 == 0.5 and wf.sharpe_oos == 0.9
    flat = DrawdownBand.from_lab_report(
        {"sharpe_oos": 0.82, "max_drawdown_p95": 0.24, "max_drawdown_p99": 0.31}
    )
    assert (flat.max_drawdown.p95, flat.max_drawdown.p99, flat.sharpe_oos) == (0.24, 0.31, 0.82)
    assert DrawdownBand.from_lab_report(None) is None
    assert DrawdownBand.from_lab_report({"montecarlo": {"max_drawdown": {"p95": 1}}}) is None
    assert DrawdownBand.from_lab_report({"sharpe_oos": 1}) is None


# ---------------------------------------------------------------------------------------
# PostgREST simulado: registro y tablas de referencia por HTTP
# ---------------------------------------------------------------------------------------
class FakePostgREST:
    """Lo mínimo de PostgREST para ``strategies``, transiciones y las tablas de referencia."""

    def __init__(self, entered_at: datetime) -> None:
        ts = entered_at.isoformat()
        self.strategy = {
            "id": str(uuid4()),
            "name": "trend",
            "state": "active",
            "params": {},
            "lab_report": LAB,
            "live_metrics": {},
            "state_changed_at": ts,
            "created_at": ts,
            "updated_at": ts,
        }
        self.transitions = [
            {"id": 1, "from_state": "lab", "to_state": "shadow", "actor": "system",
             "reason": "pasa criterios", "created_at": ts},
            {"id": 2, "from_state": "shadow", "to_state": "active", "actor": HUMAN,
             "reason": "7 días", "created_at": ts},
        ]  # fmt: skip
        self.tables: dict[str, list[dict[str, Any]]] = {
            "reference_trades": [],
            "reference_equity": [],
        }
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/rest/v1/")
        params = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        body = json.loads(request.content) if request.content else None
        if path == "strategies" and request.method == "GET":
            want = params.get("state", "")
            ok = not want or self.strategy["state"] in want.removeprefix("in.(").rstrip(")")
            ok = ok and params.get("name", f"eq.{self.strategy['name']}") == "eq.trend"
            return httpx.Response(200, json=[self.strategy] if ok else [])
        if path == "strategies" and request.method == "PATCH":
            self.strategy.update(body)
            return httpx.Response(200, json=[self.strategy])
        if path == "strategy_transitions" and request.method == "GET":
            return httpx.Response(200, json=self.transitions)
        if path == "strategy_transitions" and request.method == "POST":
            if body["from_state"] != self.strategy["state"]:
                return httpx.Response(409, json={"code": "40001", "message": "stale"})
            now = datetime.now(UTC).isoformat()
            row = {**body, "id": len(self.transitions) + 1, "created_at": now}
            self.transitions.append(row)
            self.strategy["state"] = body["to_state"]
            self.strategy["state_changed_at"] = row["created_at"]
            return httpx.Response(201, json=[row])
        if path in self.tables and request.method == "POST":
            self.tables[path].extend(body)
            return httpx.Response(201)
        return httpx.Response(404, json={"message": f"ruta no simulada: {path}"})


def test_end_to_end_with_simulated_postgrest(base):
    fake = FakePostgREST(base)
    client = httpx.Client(transport=httpx.MockTransport(fake))
    registry = StrategyRegistry(PostgRESTBackend("http://sb.test", "eyJx", client=client))
    store = PostgRESTReferenceStore("http://sb.test", "eyJx", client=client)
    source = ListSignalSource()
    svc = make_service(registry, store, source, sizing={"trend": "full"})
    source.push(sig("trend", base + timedelta(hours=1), 100.0, timeframe=Timeframe.D1))
    source.push(sig("trend", base + timedelta(hours=2), 85.0, Direction.FLAT))
    svc.run_once(base + timedelta(hours=3))

    assert fake.strategy["state"] == "probation"
    assert fake.transitions[-1]["actor"] == "system"
    assert fake.transitions[-1]["from_state"] == "active"
    (trade,) = fake.tables["reference_trades"]
    assert trade["state"] == "active" and trade["pnl"] < 0
    day = (base + timedelta(hours=3)).date().isoformat()
    assert fake.tables["reference_equity"][0]["day"] == day
    metrics = fake.strategy["live_metrics"]
    assert metrics["state"] == "probation" and metrics["trades"] == 1
    assert metrics["ledger"]["started_at"] == base.isoformat()
    # ninguna llamada fuera de Supabase
    assert {r.url.host for r in fake.requests} == {"sb.test"}
