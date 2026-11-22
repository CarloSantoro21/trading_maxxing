"""Pruebas del StrategyRegistry: ciclo de vida, backend en memoria y API en Workers."""

import json
import threading
from uuid import UUID, uuid4

import httpx
import pytest
import respx
from tm_agents.registry import (
    ALLOWED_TRANSITIONS,
    HUMAN_ONLY_TRANSITIONS,
    SYSTEM_ACTOR,
    InMemoryBackend,
    RegistryError,
    StaleStateError,
    StrategyExistsError,
    StrategyNotFoundError,
    StrategyRecord,
    StrategyRegistry,
    TransitionError,
    WorkerApiBackend,
    is_allowed_transition,
    normalize_actor,
    requires_human,
    validate_transition,
)
from tm_agents.strategy_stats import StrategyState as S
from tm_shared.api_client import WorkerApiClient
from tm_shared.fake_api import FakeWorkerApi

HUMAN = UUID("11111111-1111-1111-1111-111111111111")

# Transiciones permitidas, escritas a mano a partir de la propuesta (sección 3.3) y de la
# migración: si alguien cambia ALLOWED_TRANSITIONS sin querer, esta prueba lo detecta.
EXPECTED = {
    (S.RESEARCH, S.LAB),
    (S.RESEARCH, S.RETIRED),
    (S.LAB, S.SHADOW),
    (S.LAB, S.RESEARCH),
    (S.LAB, S.RETIRED),
    (S.SHADOW, S.ACTIVE),
    (S.SHADOW, S.LAB),
    (S.SHADOW, S.RETIRED),
    (S.ACTIVE, S.PROBATION),
    (S.ACTIVE, S.RETIRED),
    (S.PROBATION, S.ACTIVE),
    (S.PROBATION, S.RETIRED),
    (S.RETIRED, S.LAB),
}
ALL_PAIRS = [(a, b) for a in S for b in S]


@pytest.fixture
def registry():
    return StrategyRegistry(InMemoryBackend())


def walk(registry, name, *states, actor=SYSTEM_ACTOR):
    """Lleva la estrategia por los estados dados; las promociones las firma HUMAN."""
    for to in states:
        who = HUMAN if to is S.ACTIVE else actor
        registry.transition(name, to, who, f"a {to.value}")


# --- reglas ------------------------------------------------------------------------------
def test_allowed_transitions_match_lifecycle():
    got = {(a, b) for a, targets in ALLOWED_TRANSITIONS.items() for b in targets}
    assert got == EXPECTED
    assert set(ALLOWED_TRANSITIONS) == set(S)


@pytest.mark.parametrize(("a", "b"), ALL_PAIRS)
def test_is_allowed_transition_all_pairs(a, b):
    assert is_allowed_transition(a, b) is ((a, b) in EXPECTED)


def test_human_only_are_the_promotions_to_active():
    assert {(S.SHADOW, S.ACTIVE), (S.PROBATION, S.ACTIVE)} == HUMAN_ONLY_TRANSITIONS
    assert requires_human(S.SHADOW, S.ACTIVE)
    assert requires_human("probation", "active")
    assert not requires_human(S.ACTIVE, S.PROBATION)
    # Toda entrada a active es una promoción
    assert {p for p in EXPECTED if p[1] is S.ACTIVE} == HUMAN_ONLY_TRANSITIONS


@pytest.mark.parametrize(
    ("actor", "expected"),
    [
        ("system", "system"),
        (HUMAN, str(HUMAN)),
        ("11111111-1111-1111-1111-111111111111", str(HUMAN)),
        ("AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
    ],
)
def test_normalize_actor(actor, expected):
    assert normalize_actor(actor) == expected


@pytest.mark.parametrize("actor", ["robot", "", "System", None, 42])
def test_normalize_actor_rejects_garbage(actor):
    with pytest.raises(TransitionError, match="actor inválido"):
        normalize_actor(actor)


def test_validate_transition_normalizes_and_strips():
    assert validate_transition(S.SHADOW, S.ACTIVE, HUMAN, "  ok  ") == (str(HUMAN), "ok")


@pytest.mark.parametrize("reason", ["", "   ", None, "x" * 1001])
def test_validate_transition_needs_reason(reason):
    with pytest.raises(TransitionError, match="razón"):
        validate_transition(S.RESEARCH, S.LAB, SYSTEM_ACTOR, reason)


def test_validate_transition_message_lists_allowed_targets():
    with pytest.raises(TransitionError, match=r"lab -> active .*desde lab solo: research"):
        validate_transition(S.LAB, S.ACTIVE, HUMAN, "atajo")


# --- registro en memoria -----------------------------------------------------------------
def test_register_starts_in_research(registry):
    rec = registry.register("trend", {"lookbacks": [20, 50]})
    assert rec.state is S.RESEARCH
    assert rec.params == {"lookbacks": [20, 50]}
    assert rec.lab_report is None
    assert registry.get("trend") == rec
    assert registry.history("trend") == []


@pytest.mark.parametrize("name", ["Trend", "1trend", "", "bad name", "a" * 65, "trend-x"])
def test_register_rejects_bad_names(registry, name):
    with pytest.raises(ValueError, match="nombre inválido"):
        registry.register(name)


def test_register_twice_fails_and_ensure_is_idempotent(registry):
    first = registry.ensure("trend", {"a": 1})
    assert registry.ensure("trend", {"a": 2}) == first
    with pytest.raises(StrategyExistsError):
        registry.register("trend")


def test_get_unknown_raises(registry):
    assert registry.find("nope") is None
    with pytest.raises(StrategyNotFoundError):
        registry.get("nope")
    with pytest.raises(StrategyNotFoundError):
        registry.transition("nope", S.LAB, SYSTEM_ACTOR, "x")
    with pytest.raises(StrategyNotFoundError):
        registry.record_live_metrics("nope", {})


def test_full_lifecycle_and_history(registry):
    registry.register("trend")
    walk(registry, "trend", S.LAB, S.SHADOW, S.ACTIVE, S.PROBATION, S.ACTIVE)
    walk(registry, "trend", S.PROBATION, S.RETIRED, S.LAB)
    assert registry.get("trend").state is S.LAB

    history = registry.history("trend")
    assert [(t.from_state, t.to_state) for t in history] == [
        (S.RESEARCH, S.LAB),
        (S.LAB, S.SHADOW),
        (S.SHADOW, S.ACTIVE),
        (S.ACTIVE, S.PROBATION),
        (S.PROBATION, S.ACTIVE),
        (S.ACTIVE, S.PROBATION),
        (S.PROBATION, S.RETIRED),
        (S.RETIRED, S.LAB),
    ]
    assert [t.is_human for t in history] == [False, False, True, False, True, False, False, False]
    assert all(t.strategy == "trend" and t.reason for t in history)
    assert [t.id for t in history] == sorted(t.id for t in history)
    assert history[2].actor == str(HUMAN)


def test_state_changed_at_moves_on_transition(registry):
    before = registry.register("trend").state_changed_at
    t = registry.transition("trend", S.LAB, SYSTEM_ACTOR, "lab")
    rec = registry.get("trend")
    assert rec.state_changed_at == t.created_at >= before


@pytest.mark.parametrize("start", [S.SHADOW, S.PROBATION])
def test_promotion_by_system_is_rejected(registry, start):
    registry.register("trend")
    path = {
        S.SHADOW: [S.LAB, S.SHADOW],
        S.PROBATION: [S.LAB, S.SHADOW, S.ACTIVE, S.PROBATION],
    }[start]
    walk(registry, "trend", *path)
    n = len(registry.history("trend"))
    with pytest.raises(TransitionError, match="actor humano"):
        registry.transition("trend", S.ACTIVE, SYSTEM_ACTOR, "auto")
    assert registry.get("trend").state is start
    assert len(registry.history("trend")) == n  # nada rechazado entra al historial
    registry.transition("trend", S.ACTIVE, str(HUMAN), "aprobada en el dashboard")
    assert registry.get("trend").state is S.ACTIVE


def test_demotions_can_be_manual_or_automatic(registry):
    registry.register("a")
    registry.register("b")
    walk(registry, "a", S.LAB, S.SHADOW, S.ACTIVE)
    walk(registry, "b", S.LAB, S.SHADOW, S.ACTIVE)
    registry.transition("a", S.PROBATION, SYSTEM_ACTOR, "drawdown > p95")
    registry.transition("b", S.RETIRED, HUMAN, "retiro manual")
    assert registry.get("a").state is S.PROBATION
    assert registry.get("b").state is S.RETIRED


@pytest.mark.parametrize(("a", "b"), [p for p in ALL_PAIRS if p not in EXPECTED])
def test_disallowed_transitions_rejected_in_registry(a, b):
    backend = InMemoryBackend()
    backend._records["s"] = StrategyRecord(name="s", state=a)
    backend._history["s"] = []
    registry = StrategyRegistry(backend)
    with pytest.raises(TransitionError, match="no permitida"):
        registry.transition("s", b, HUMAN, "intento")
    assert registry.get("s").state is a
    assert registry.history("s") == []


def test_queries_by_state(registry):
    for name in ("trend", "kronos", "momentum", "news", "old"):
        registry.register(name)
    walk(registry, "trend", S.LAB, S.SHADOW, S.ACTIVE)
    walk(registry, "kronos", S.LAB, S.SHADOW)
    walk(registry, "momentum", S.LAB, S.SHADOW, S.ACTIVE, S.PROBATION)
    walk(registry, "old", S.RETIRED)

    def names(records):
        return [r.name for r in records]

    assert names(registry.active()) == ["trend"]
    assert names(registry.shadow()) == ["kronos"]
    assert names(registry.trading()) == ["momentum", "trend"]
    assert names(registry.list()) == ["kronos", "momentum", "news", "old", "trend"]
    assert names(registry.list(S.RESEARCH, S.RETIRED)) == ["news", "old"]


def test_updates_do_not_touch_state(registry):
    registry.register("trend")
    registry.record_lab_report("trend", {"sharpe_oos": 0.8})
    registry.record_live_metrics("trend", {"drawdown": 0.02})
    rec = registry.set_params("trend", {"lookbacks": [10]})
    assert rec.state is S.RESEARCH
    assert rec.lab_report == {"sharpe_oos": 0.8}
    assert rec.live_metrics == {"drawdown": 0.02}
    assert rec.params == {"lookbacks": [10]}
    assert registry.history("trend") == []


def test_stale_state_is_detected():
    backend = InMemoryBackend()
    registry = StrategyRegistry(backend)
    stale = registry.register("trend")
    registry.transition("trend", S.LAB, SYSTEM_ACTOR, "lab")
    with pytest.raises(StaleStateError):
        backend.append_transition(stale, S.RETIRED, SYSTEM_ACTOR, "con estado viejo")
    assert registry.get("trend").state is S.LAB


def test_concurrent_transitions_only_one_wins():
    backend = InMemoryBackend()
    registry = StrategyRegistry(backend)
    registry.register("trend")
    walk(registry, "trend", S.LAB, S.SHADOW, S.ACTIVE)
    snapshot = registry.get("trend")
    results = []

    def go(to):
        try:
            backend.append_transition(snapshot, to, SYSTEM_ACTOR, "carrera")
            results.append("ok")
        except StaleStateError:
            results.append("stale")

    threads = [threading.Thread(target=go, args=(t,)) for t in (S.PROBATION, S.RETIRED) * 4]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 1
    assert len(registry.history("trend")) == 4


def test_default_backend_is_in_memory():
    assert isinstance(StrategyRegistry().backend, InMemoryBackend)


def test_record_and_transition_coerce_strings():
    rec = StrategyRecord(name="x", state="lab")
    assert rec.state is S.LAB


# --- API en Workers (/internal/strategies) ------------------------------------------------
URL = "https://tm-api.test"
API = f"{URL}/internal/strategies"
TOKEN = "service-token"
SID = uuid4()


def row(name="trend", state="research", **kw):
    base = {
        "id": str(SID),
        "name": name,
        "state": state,
        "params": {"lookbacks": [20]},
        "lab_report": None,
        "live_metrics": {},
        "state_changed_at": "2026-11-07T10:00:00.000Z",
        "created_at": "2026-11-07T10:00:00.000Z",
        "updated_at": "2026-11-07T10:00:00.000Z",
        "shadow_days_clean": None,
    }
    base.update(kw)
    return base


def trow(frm, to, actor="system", reason="r", id_=1):
    return {
        "id": id_,
        "strategy": "trend",
        "strategy_id": str(SID),
        "from_state": frm,
        "to_state": to,
        "actor": actor,
        "reason": reason,
        "created_at": "2026-11-07T11:00:00.123Z",
    }


@pytest.fixture
def api():
    backend = WorkerApiBackend(WorkerApiClient(URL + "/", TOKEN, sleep=lambda _s: None))
    yield backend
    backend.close()


@respx.mock
def test_api_get_sends_service_token(api):
    route = respx.get(f"{API}/trend").mock(return_value=httpx.Response(200, json=row()))
    rec = StrategyRegistry(api).get("trend")
    assert rec.name == "trend" and rec.state is S.RESEARCH and rec.id == SID
    assert rec.params == {"lookbacks": [20]} and rec.live_metrics == {}
    req = route.calls.last.request
    assert req.headers["x-service-token"] == TOKEN
    assert "authorization" not in req.headers


@respx.mock
def test_api_get_missing_returns_none(api):
    respx.get(f"{API}/nope").mock(
        return_value=httpx.Response(404, json={"detail": "no", "code": "not_found"})
    )
    assert api.get("nope") is None
    with pytest.raises(StrategyNotFoundError):
        StrategyRegistry(api).get("nope")


@respx.mock
def test_api_list_filters_by_state(api):
    route = respx.get(API).mock(
        return_value=httpx.Response(200, json=[row("a", "active"), row("b", "probation")])
    )
    recs = StrategyRegistry(api).trading()
    assert [r.name for r in recs] == ["a", "b"]
    assert route.calls.last.request.url.params["state"] == "active,probation"
    StrategyRegistry(api).list()
    assert "state" not in route.calls.last.request.url.params


@respx.mock
def test_api_register_posts_and_returns_record(api):
    route = respx.post(API).mock(return_value=httpx.Response(201, json=row(params={"k": 1})))
    rec = StrategyRegistry(api).register("trend", {"k": 1})
    assert rec.params == {"k": 1}
    assert json.loads(route.calls.last.request.content) == {"name": "trend", "params": {"k": 1}}


@respx.mock
def test_api_register_duplicate_maps_to_exists(api):
    respx.post(API).mock(
        return_value=httpx.Response(
            409, json={"detail": "ya existe 'trend'", "code": "strategy_exists"}
        )
    )
    with pytest.raises(StrategyExistsError, match="ya existe"):
        StrategyRegistry(api).register("trend")


@respx.mock
def test_api_transition_posts_observed_state(api):
    respx.get(f"{API}/trend").mock(return_value=httpx.Response(200, json=row(state="shadow")))
    route = respx.post(f"{API}/trend/transitions").mock(
        return_value=httpx.Response(
            201, json=trow("shadow", "active", str(HUMAN), "aprobada", id_=7)
        )
    )
    t = StrategyRegistry(api).transition("trend", S.ACTIVE, HUMAN, " aprobada ")
    assert (t.from_state, t.to_state, t.actor, t.id) == (S.SHADOW, S.ACTIVE, str(HUMAN), 7)
    assert t.created_at.microsecond == 123000
    assert json.loads(route.calls.last.request.content) == {
        "from_state": "shadow",
        "to_state": "active",
        "actor": str(HUMAN),
        "reason": "aprobada",
    }


@respx.mock
def test_api_transition_validates_before_calling(api):
    respx.get(f"{API}/trend").mock(return_value=httpx.Response(200, json=row(state="shadow")))
    route = respx.post(f"{API}/trend/transitions")
    with pytest.raises(TransitionError, match="actor humano"):
        StrategyRegistry(api).transition("trend", S.ACTIVE, SYSTEM_ACTOR, "auto")
    assert not route.called


@pytest.mark.parametrize(
    ("status", "code", "exc"),
    [
        (409, "stale_state", StaleStateError),
        (409, "transition_not_allowed", TransitionError),
        (422, "check_violation", TransitionError),
        (404, "not_found", StrategyNotFoundError),
        (422, "foreign_key_violation", StrategyNotFoundError),
        (409, "unique_violation", StrategyExistsError),
        (400, None, RegistryError),
    ],
)
@respx.mock
def test_api_transition_maps_contract_errors(api, status, code, exc):
    respx.get(f"{API}/trend").mock(return_value=httpx.Response(200, json=row()))
    body = {"detail": "boom", **({"code": code} if code else {})}
    respx.post(f"{API}/trend/transitions").mock(return_value=httpx.Response(status, json=body))
    with pytest.raises(exc, match="boom"):
        StrategyRegistry(api).transition("trend", S.LAB, SYSTEM_ACTOR, "lab")


@respx.mock
def test_api_retries_idempotent_reads_and_reports_network_errors(api):
    route = respx.get(f"{API}/trend").mock(
        side_effect=[httpx.Response(502, text="bad gateway"), httpx.Response(200, json=row())]
    )
    assert api.get("trend").name == "trend"
    assert route.call_count == 2
    respx.get(f"{API}/trend/transitions").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(RegistryError, match="no se pudo llamar"):
        api.history(StrategyRecord(name="trend"))


@respx.mock
def test_api_history_in_order(api):
    respx.get(f"{API}/trend").mock(return_value=httpx.Response(200, json=row()))
    respx.get(f"{API}/trend/transitions").mock(
        return_value=httpx.Response(
            200, json=[trow("research", "lab", id_=1), trow("lab", "shadow", id_=2)]
        )
    )
    history = StrategyRegistry(api).history("trend")
    assert [(t.from_state, t.to_state) for t in history] == [
        (S.RESEARCH, S.LAB),
        (S.LAB, S.SHADOW),
    ]


@respx.mock
def test_api_update_patches_and_handles_missing(api):
    route = respx.patch(f"{API}/trend").mock(
        return_value=httpx.Response(200, json=row(live_metrics={"drawdown": 0.1}))
    )
    respx.patch(f"{API}/nope").mock(
        return_value=httpx.Response(404, json={"detail": "no", "code": "not_found"})
    )
    rec = StrategyRegistry(api).record_live_metrics("trend", {"drawdown": 0.1})
    assert rec.live_metrics == {"drawdown": 0.1}
    assert json.loads(route.calls.last.request.content) == {"live_metrics": {"drawdown": 0.1}}
    with pytest.raises(StrategyNotFoundError):
        StrategyRegistry(api).record_lab_report("nope", {"a": 1})


@respx.mock
def test_api_quotes_names_in_paths(api):
    route = respx.get(f"{API}/a%20b").mock(return_value=httpx.Response(200, json=row("a b")))
    assert api.get("a b").name == "a b"
    assert route.called


def test_api_against_fake_worker_full_lifecycle():
    """Ciclo completo contra la API simulada (mismas respuestas que el Worker)."""
    fake = FakeWorkerApi()
    reg = StrategyRegistry(WorkerApiBackend(fake.client()))
    reg.register("trend", {"k": 1})
    reg.transition("trend", S.LAB, SYSTEM_ACTOR, "lab")
    reg.transition("trend", S.SHADOW, SYSTEM_ACTOR, "pasa el lab")
    assert reg.get("trend").state is S.SHADOW
    assert [t.to_state for t in reg.history("trend")] == [S.LAB, S.SHADOW]
    fake.strategies["trend"]["state"] = "active"  # otro proceso la cambió
    with pytest.raises(StaleStateError):
        reg.backend.append_transition(
            StrategyRecord(name="trend", state=S.SHADOW), S.ACTIVE, str(HUMAN), "x"
        )


def test_api_from_env_and_validation(monkeypatch):
    backend = WorkerApiBackend.from_env({"TM_API_URL": URL + "/internal", "TM_SERVICE_TOKEN": "t"})
    assert backend.client.base_url == URL
    backend.close()
    with pytest.raises(ValueError, match="TM_API_URL"):
        WorkerApiBackend.from_env({})
    with pytest.raises(ValueError, match="TM_SERVICE_TOKEN"):
        WorkerApiBackend.from_env({"TM_API_URL": URL})
    monkeypatch.setenv("TM_API_URL", URL)
    monkeypatch.setenv("TM_SERVICE_TOKEN", "t")
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "id.access")
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", "s3cr3t")
    backend = WorkerApiBackend.from_env()
    assert backend.client.headers["CF-Access-Client-Id"] == "id.access"
    backend.close()
