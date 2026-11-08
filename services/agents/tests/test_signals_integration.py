"""Integración de ``tm:signals``: una señal publicada una vez la consumen todos (#19 + #57).

La capa compartida publica cada evento una sola vez (``RedisSignalSink``); el BotRunner (por
el ``OrchestratorWorker``) y el ``ReferenceService`` lo leen del mismo stream, cada uno con su
cursor, y con el mismo modelo (:class:`tm_agents.signals.SignalEvent`). También se revisa que
el LifecycleMonitor (que corre dentro del ReferenceService) y las curvas de sensibilidad del
orquestador no se pisen: el monitor solo escribe ``state`` y ``live_metrics``; las curvas y el
p95 salen de ``lab_report``, que nadie reescribe.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import fakeredis
import pytest
from tm_agents.orchestrator.bot import BotConfig, BotProfile
from tm_agents.orchestrator.exposure import exposure_for_level, sensitivity_for
from tm_agents.orchestrator.runtime import OrchestratorWorker
from tm_agents.orchestrator.streams import MemorySink
from tm_agents.orchestrator.supervisor import BotSupervisor, InMemoryBotRepository
from tm_agents.reference import REFERENCE_CHECKPOINT, InMemoryReferenceStore, ReferenceService
from tm_agents.registry import StrategyRegistry
from tm_agents.signals import (
    LIVE_STATES,
    SIGNALS_STREAM,
    RedisSignalSink,
    RedisSignalSource,
    SignalEvent,
    catalog_from_records,
)
from tm_agents.strategy_stats import StrategyState as S
from tm_shared.contracts import Direction, Horizon, Signal, SimResult, Timeframe

HUMAN = "11111111-1111-1111-1111-111111111111"
SIM_OK = SimResult(
    p_take_profit=0.3, p_stop_loss=0.1, p_open=0.6, expected_value=0.4, n_paths=100, source="x"
)
#: Curva de sensibilidad del reporte: con 50 % invertido el p95 baja menos que a la mitad.
CURVE = [[1.0, 0.30], [0.5, 0.17], [0.1, 0.035]]
#: p95 de 30 % y p99 de 50 %: una caída de 40 % en el ledger de referencia degrada.
LAB = {
    "aggregate": {"volatility": 0.4, "sharpe": 1.1},
    "montecarlo": {"max_drawdown": {"p5": 0.1, "p50": 0.2, "p95": 0.30, "p99": 0.5}},
    "exposure_sensitivity": CURVE,
}


@pytest.fixture
def base() -> datetime:
    # Después de "ahora": el historial del registro en memoria usa el reloj real.
    return datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=1)


def active_registry(base: datetime) -> StrategyRegistry:
    registry = StrategyRegistry()
    registry.register("trend")
    for to in (S.LAB, S.SHADOW, S.ACTIVE):
        registry.transition("trend", to, HUMAN if to is S.ACTIVE else "system", "prueba")
    registry.backend.update("trend", {"state_changed_at": base, "lab_report": LAB})
    return registry


def published(registry: StrategyRegistry, at: datetime, price: str, long: bool) -> SignalEvent:
    """Lo que publica la capa compartida: evento con catálogo del registro y gate."""
    signal = Signal(
        symbol="BTCUSDT",
        direction=Direction.LONG if long else Direction.FLAT,
        confidence=1.0 if long else 0.0,
        horizon=Horizon.MEDIUM,
        timeframe=Timeframe.H4,
        source="trend",
        generated_at=at,
    )
    record = registry.get("trend")
    event = SignalEvent.from_signal(
        signal,
        price,
        state=record.state,
        catalog=catalog_from_records(registry.list(*LIVE_STATES)),
    )
    return event.model_copy(update={"sim": SIM_OK if long else None})


def consumers(client, registry):
    decisions = MemorySink()
    profile = BotProfile(risk_level=3, capital=Decimal("10000"), max_drawdown=0.2)
    supervisor = BotSupervisor(InMemoryBotRepository([BotConfig("bot-a", profile)]), decisions)
    worker = OrchestratorWorker(None, supervisor, RedisSignalSource(client))
    reference = ReferenceService(
        registry,
        InMemoryReferenceStore(),
        RedisSignalSource(client, checkpoint_key=REFERENCE_CHECKPOINT),
        symbols=("BTCUSDT",),
    )
    return worker, reference, decisions


def test_one_signal_is_consumed_by_bot_runner_and_reference_service(base):
    client = fakeredis.FakeRedis()
    registry = active_registry(base)
    worker, reference, decisions = consumers(client, registry)
    event = published(registry, base + timedelta(hours=4), "100", long=True)

    RedisSignalSink(client).publish(event)  # una sola vez
    assert client.xlen(SIGNALS_STREAM) == 1

    stats = worker.tick()
    assert stats["events_consumed"] == 1 and stats["decisions"] == 1
    (envelope,) = decisions.items
    assert envelope.bot_id == "bot-a" and envelope.sleeve == "trend"
    assert envelope.id == f"bot-a:{event.id}"

    assert reference.run_once(base + timedelta(hours=5)) == 1
    ledger = reference.tracked["trend"].ledger
    assert ledger.positions_value > 0 and ledger.last_signal_at == event.bar_close
    # el ReferenceService confirmó su cursor; el stream sigue con la entrada para otros
    last_id = client.get(REFERENCE_CHECKPOINT).decode()
    assert last_id == worker.signals.last_id
    assert client.xlen(SIGNALS_STREAM) == 1
    # y nadie vuelve a leerla
    assert worker.tick()["events_consumed"] == 0
    assert reference.run_once(base + timedelta(hours=6)) == 0


def test_lifecycle_monitor_and_sensitivity_curves_do_not_overlap(base):
    client = fakeredis.FakeRedis()
    registry = active_registry(base)
    worker, reference, decisions = consumers(client, registry)
    sink = RedisSignalSink(client)
    before = registry.get("trend").lab_report

    sink.publish(published(registry, base + timedelta(hours=4), "100", long=True))
    sink.publish(published(registry, base + timedelta(hours=8), "60", long=False))
    worker.tick()
    reference.run_once(base + timedelta(hours=9))

    # El monitor juzga el ledger de referencia (a tamaño completo) contra el p95 a k = 1 del
    # laboratorio y degrada; el bot dimensionó con la curva, que es otra lectura del reporte.
    record = registry.get("trend")
    assert record.state is S.PROBATION
    assert [t.to_state for _, t in reference.transitions] == [S.PROBATION]
    assert record.lab_report == before  # el monitor no reescribe lab_report
    assert "ledger" in record.live_metrics
    (snapshot,) = catalog_from_records(registry.list(*LIVE_STATES))
    assert snapshot.state is S.PROBATION
    assert snapshot.sensitivity == tuple(tuple(p) for p in CURVE)
    assert snapshot.max_drawdown_p95 == 0.30
    exposure = exposure_for_level(3, 0.30, sensitivity_for("trend", snapshot.sensitivity))
    # nivel 3 = 20 % de drawdown (RISK_TABLE): por la curva, no por la regla proporcional
    assert exposure == pytest.approx(0.5 + (2 / 3 - 0.17 / 0.30) * 0.5 / (1 - 0.17 / 0.30))
    assert exposure < 2 / 3
    # y la decisión del bot sí salió de esa exposición
    (buy, sell) = decisions.items
    assert buy.decision.rationale.weights["exposure"] == pytest.approx(exposure)
    assert sell.target_quantity == 0
