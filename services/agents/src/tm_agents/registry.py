"""StrategyRegistry: estado del ciclo de vida de cada estrategia (propuesta, sección 3.3).

El código de una estrategia entra por PR; su estado es un dato que vive en la tabla
``strategies`` de D1 (API en Workers), con su historial en ``strategy_transitions``::

    research → lab → shadow → active → probation → retired
                 ▲                                    │
                 └──────────── revalidación ──────────┘

Reglas (iguales a las de la migración ``apps/api/migrations/0002_strategies.sql``):

- Solo existen las transiciones de :data:`ALLOWED_TRANSITIONS`.
- Las promociones (``shadow → active`` y ``probation → active``) solo las hace una persona:
  el actor es el ``user_id`` (UUID) de quien aprueba, nunca ``"system"``.
- Las degradaciones y los retiros pueden ser automáticos (``"system"``) o manuales.
- Cada cambio de estado queda en el historial con quién, cuándo y por qué.

Hay dos backends: :class:`InMemoryBackend` (pruebas y backtests) y
:class:`WorkerApiBackend` (API en Cloudflare Workers, ``/internal/strategies``)::

    from tm_agents.registry import SYSTEM_ACTOR, StrategyRegistry, WorkerApiBackend
    from tm_agents.strategy_stats import StrategyState

    registry = StrategyRegistry(WorkerApiBackend.from_env())
    registry.transition("trend", StrategyState.PROBATION, SYSTEM_ACTOR, "drawdown > p95")
    registry.history("trend")
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Protocol
from urllib.parse import quote
from uuid import UUID, uuid4

from tm_shared.api_client import WorkerApiClient, WorkerApiError

from tm_agents.strategy_stats import StrategyState

S = StrategyState

#: Actor de los cambios automáticos (LifecycleMonitor, laboratorio).
SYSTEM_ACTOR = "system"

#: Transiciones permitidas: estado de origen -> estados de destino.
ALLOWED_TRANSITIONS: Mapping[StrategyState, frozenset[StrategyState]] = MappingProxyType(
    {
        S.RESEARCH: frozenset({S.LAB, S.RETIRED}),
        S.LAB: frozenset({S.SHADOW, S.RESEARCH, S.RETIRED}),
        S.SHADOW: frozenset({S.ACTIVE, S.LAB, S.RETIRED}),
        S.ACTIVE: frozenset({S.PROBATION, S.RETIRED}),
        S.PROBATION: frozenset({S.ACTIVE, S.RETIRED}),
        S.RETIRED: frozenset({S.LAB}),
    }
)

#: Promociones que solo puede aprobar una persona.
HUMAN_ONLY_TRANSITIONS: frozenset[tuple[StrategyState, StrategyState]] = frozenset(
    {(S.SHADOW, S.ACTIVE), (S.PROBATION, S.ACTIVE)}
)

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MAX_REASON = 1000

Actor = str | UUID


# ---------------------------------------------------------------------------------------
# Errores
# ---------------------------------------------------------------------------------------
class RegistryError(Exception):
    """Error genérico del registro (por ejemplo, una respuesta inesperada de la API)."""


class StrategyNotFoundError(RegistryError, KeyError):
    """La estrategia no está en el registro."""


class StrategyExistsError(RegistryError):
    """Ya hay una estrategia registrada con ese nombre."""


class TransitionError(RegistryError, ValueError):
    """La transición no está permitida o el actor no puede hacerla."""


class StaleStateError(TransitionError):
    """El estado cambió entre la lectura y la transición (otra escritura llegó antes)."""


# ---------------------------------------------------------------------------------------
# Reglas
# ---------------------------------------------------------------------------------------
def is_allowed_transition(from_state: StrategyState, to_state: StrategyState) -> bool:
    """True si ``from_state → to_state`` está en el ciclo de vida."""
    return S(to_state) in ALLOWED_TRANSITIONS[S(from_state)]


def requires_human(from_state: StrategyState, to_state: StrategyState) -> bool:
    """True si la transición es una promoción que solo aprueba una persona."""
    return (S(from_state), S(to_state)) in HUMAN_ONLY_TRANSITIONS


def normalize_actor(actor: Actor) -> str:
    """Devuelve ``"system"`` o el UUID del usuario en minúsculas; rechaza lo demás."""
    if isinstance(actor, UUID):
        return str(actor)
    if actor == SYSTEM_ACTOR:
        return SYSTEM_ACTOR
    try:
        return str(UUID(actor))
    except (TypeError, ValueError, AttributeError):
        raise TransitionError(
            f"actor inválido: {actor!r} (usa '{SYSTEM_ACTOR}' o el user_id de la persona)"
        ) from None


def validate_transition(
    from_state: StrategyState, to_state: StrategyState, actor: Actor, reason: str
) -> tuple[str, str]:
    """Valida la transición y devuelve ``(actor, reason)`` normalizados.

    Raises:
        TransitionError: si la transición no existe, si es una promoción hecha por
            ``"system"``, o si el actor o la razón no son válidos.
    """
    from_state, to_state = S(from_state), S(to_state)
    who = normalize_actor(actor)
    if not is_allowed_transition(from_state, to_state):
        allowed = ", ".join(sorted(s.value for s in ALLOWED_TRANSITIONS[from_state]))
        raise TransitionError(
            f"transición no permitida: {from_state.value} -> {to_state.value} "
            f"(desde {from_state.value} solo: {allowed})"
        )
    if requires_human(from_state, to_state) and who == SYSTEM_ACTOR:
        raise TransitionError(
            f"la promoción {from_state.value} -> {to_state.value} requiere un actor humano"
        )
    why = (reason or "").strip()
    if not why:
        raise TransitionError("la transición necesita una razón")
    if len(why) > _MAX_REASON:
        raise TransitionError(f"la razón no puede pasar de {_MAX_REASON} caracteres")
    return who, why


def _validate_name(name: str) -> str:
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ValueError(
            f"nombre inválido: {name!r} (minúsculas, dígitos y '_', empezando con letra)"
        )
    return name


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------------------
@dataclass(frozen=True)
class StrategyRecord:
    """Una fila de ``strategies``.

    Attributes:
        name: nombre con el que la estrategia está registrada (``register_strategy``).
        state: estado actual del ciclo de vida.
        params: parámetros con los que corre.
        lab_report: último reporte del laboratorio (walk-forward + Monte Carlo), si hay.
        live_metrics: métricas en vivo que vigila el LifecycleMonitor.
        id: identificador en la base.
        state_changed_at: cuándo entró al estado actual.
    """

    name: str
    state: StrategyState = S.RESEARCH
    params: Mapping[str, Any] = field(default_factory=dict)
    lab_report: Mapping[str, Any] | None = None
    live_metrics: Mapping[str, Any] = field(default_factory=dict)
    id: UUID = field(default_factory=uuid4)
    state_changed_at: datetime = field(default_factory=_now)
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", S(self.state))

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> StrategyRecord:
        """Construye el registro a partir de una fila de la API (``Strategy``)."""
        return cls(
            name=row["name"],
            state=S(row["state"]),
            params=row.get("params") or {},
            lab_report=row.get("lab_report"),
            live_metrics=row.get("live_metrics") or {},
            id=UUID(str(row["id"])),
            state_changed_at=_parse_ts(row["state_changed_at"]),
            created_at=_parse_ts(row["created_at"]),
            updated_at=_parse_ts(row["updated_at"]),
        )


@dataclass(frozen=True)
class StrategyTransition:
    """Una fila de ``strategy_transitions``: quién, cuándo y por qué cambió el estado."""

    strategy: str
    from_state: StrategyState
    to_state: StrategyState
    actor: str
    reason: str
    created_at: datetime = field(default_factory=_now)
    id: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "from_state", S(self.from_state))
        object.__setattr__(self, "to_state", S(self.to_state))

    @property
    def is_human(self) -> bool:
        return self.actor != SYSTEM_ACTOR

    @classmethod
    def from_row(cls, strategy: str, row: Mapping[str, Any]) -> StrategyTransition:
        return cls(
            strategy=strategy,
            from_state=S(row["from_state"]),
            to_state=S(row["to_state"]),
            actor=row["actor"],
            reason=row["reason"],
            created_at=_parse_ts(row["created_at"]),
            id=row.get("id"),
        )


def _parse_ts(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    # La API devuelve ISO 8601 UTC con "Z"; Python 3.12 la entiende.
    return datetime.fromisoformat(value)


# ---------------------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------------------
class RegistryBackend(Protocol):
    """Almacenamiento del registro. Las reglas del ciclo de vida las aplica el registro."""

    def get(self, name: str) -> StrategyRecord | None: ...

    def list(self, states: Iterable[StrategyState] | None = None) -> list[StrategyRecord]: ...

    def create(self, name: str, params: Mapping[str, Any]) -> StrategyRecord: ...

    def update(self, name: str, fields: Mapping[str, Any]) -> StrategyRecord: ...

    def append_transition(
        self, record: StrategyRecord, to_state: StrategyState, actor: str, reason: str
    ) -> StrategyTransition:
        """Cambia el estado y guarda la transición de forma atómica.

        Debe fallar con :class:`StaleStateError` si el estado actual ya no es
        ``record.state``.
        """
        ...

    def history(self, record: StrategyRecord) -> list[StrategyTransition]: ...


class InMemoryBackend:
    """Backend en memoria, seguro entre hilos. Para pruebas, backtests y desarrollo."""

    def __init__(self) -> None:
        self._records: dict[str, StrategyRecord] = {}
        self._history: dict[str, list[StrategyTransition]] = {}
        self._lock = threading.Lock()
        self._next_id = 1

    def get(self, name: str) -> StrategyRecord | None:
        return self._records.get(name)

    def list(self, states: Iterable[StrategyState] | None = None) -> list[StrategyRecord]:
        wanted = None if states is None else {S(s) for s in states}
        return sorted(
            (r for r in self._records.values() if wanted is None or r.state in wanted),
            key=lambda r: r.name,
        )

    def create(self, name: str, params: Mapping[str, Any]) -> StrategyRecord:
        with self._lock:
            if name in self._records:
                raise StrategyExistsError(f"ya existe la estrategia '{name}'")
            record = StrategyRecord(name=name, params=dict(params))
            self._records[name] = record
            self._history[name] = []
            return record

    def update(self, name: str, fields: Mapping[str, Any]) -> StrategyRecord:
        with self._lock:
            record = self._require(name)
            record = replace(record, **fields, updated_at=_now())
            self._records[name] = record
            return record

    def append_transition(
        self, record: StrategyRecord, to_state: StrategyState, actor: str, reason: str
    ) -> StrategyTransition:
        with self._lock:
            current = self._require(record.name)
            if current.state is not record.state:
                raise StaleStateError(
                    f"'{record.name}' está en {current.state.value}, no en {record.state.value}"
                )
            now = _now()
            transition = StrategyTransition(
                strategy=record.name,
                from_state=current.state,
                to_state=S(to_state),
                actor=actor,
                reason=reason,
                created_at=now,
                id=self._next_id,
            )
            self._next_id += 1
            self._records[record.name] = replace(
                current, state=S(to_state), state_changed_at=now, updated_at=now
            )
            self._history[record.name].append(transition)
            return transition

    def history(self, record: StrategyRecord) -> list[StrategyTransition]:
        return list(self._history.get(record.name, []))

    def _require(self, name: str) -> StrategyRecord:
        try:
            return self._records[name]
        except KeyError:
            raise StrategyNotFoundError(name) from None


class WorkerApiBackend:
    """Backend sobre la API en Cloudflare Workers (``/internal/strategies``), base D1.

    La base vuelve a validar todo (checks y triggers de ``0002_strategies.sql``), así que
    aunque otro servicio escriba directo, el ciclo de vida se respeta: cada transición es un
    ``POST /internal/strategies/{name}/transitions`` y el Worker responde ``409 stale_state``
    si el estado ya no es ``from_state``.

    Args:
        client: :class:`~tm_shared.api_client.WorkerApiClient` (``TM_API_URL`` y
            ``TM_SERVICE_TOKEN``).
    """

    def __init__(self, client: WorkerApiClient) -> None:
        self.client = client

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, **kwargs: Any) -> WorkerApiBackend:
        """Lee ``TM_API_URL``, ``TM_SERVICE_TOKEN`` y los headers de Access del entorno."""
        return cls(WorkerApiClient.from_env(env, **kwargs))

    def close(self) -> None:
        self.client.close()

    # --- lectura -----------------------------------------------------------------------
    def get(self, name: str) -> StrategyRecord | None:
        try:
            row = self._call("GET", f"/internal/strategies/{quote(name)}")
        except StrategyNotFoundError:
            return None
        return StrategyRecord.from_row(row)

    def list(self, states: Iterable[StrategyState] | None = None) -> list[StrategyRecord]:
        params = {} if states is None else {"state": ",".join(S(s).value for s in states)}
        rows = self._call("GET", "/internal/strategies", params=params)
        return [StrategyRecord.from_row(r) for r in rows or []]

    def history(self, record: StrategyRecord) -> list[StrategyTransition]:
        rows = self._call("GET", f"/internal/strategies/{quote(record.name)}/transitions")
        return [StrategyTransition.from_row(record.name, r) for r in rows or []]

    # --- escritura ---------------------------------------------------------------------
    def create(self, name: str, params: Mapping[str, Any]) -> StrategyRecord:
        row = self._call(
            "POST", "/internal/strategies", json={"name": name, "params": dict(params)}
        )
        return StrategyRecord.from_row(row)

    def update(self, name: str, fields: Mapping[str, Any]) -> StrategyRecord:
        row = self._call("PATCH", f"/internal/strategies/{quote(name)}", json=dict(fields))
        return StrategyRecord.from_row(row)

    def append_transition(
        self, record: StrategyRecord, to_state: StrategyState, actor: str, reason: str
    ) -> StrategyTransition:
        row = self._call(
            "POST",
            f"/internal/strategies/{quote(record.name)}/transitions",
            json={
                "from_state": record.state.value,
                "to_state": S(to_state).value,
                "actor": actor,
                "reason": reason,
            },
        )
        return StrategyTransition.from_row(record.name, row)

    # --- HTTP --------------------------------------------------------------------------
    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            return self.client.request(method, path, **kwargs)
        except WorkerApiError as exc:
            raise _error_from_api(exc) from exc


def _error_from_api(exc: WorkerApiError) -> RegistryError:
    """Traduce los ``code`` del contrato a errores del registro."""
    message = str(exc.detail or exc)
    if exc.code == "stale_state":
        return StaleStateError(message)
    if exc.code in ("strategy_exists", "unique_violation"):
        return StrategyExistsError(message)
    if exc.code in ("transition_not_allowed", "check_violation"):
        return TransitionError(message)
    if exc.status == 404 or exc.code in ("not_found", "foreign_key_violation"):
        return StrategyNotFoundError(message)
    return RegistryError(str(exc))


# ---------------------------------------------------------------------------------------
# Registro
# ---------------------------------------------------------------------------------------
class StrategyRegistry:
    """Fachada del ciclo de vida: consulta estados y aplica transiciones validadas."""

    def __init__(self, backend: RegistryBackend | None = None) -> None:
        self.backend: RegistryBackend = backend if backend is not None else InMemoryBackend()

    # --- consultas ---------------------------------------------------------------------
    def get(self, name: str) -> StrategyRecord:
        record = self.backend.get(name)
        if record is None:
            raise StrategyNotFoundError(name)
        return record

    def find(self, name: str) -> StrategyRecord | None:
        return self.backend.get(name)

    def list(self, *states: StrategyState) -> list[StrategyRecord]:
        """Todas las estrategias, o solo las de los estados dados, ordenadas por nombre."""
        return self.backend.list(states or None)

    def active(self) -> list[StrategyRecord]:
        return self.list(S.ACTIVE)

    def shadow(self) -> list[StrategyRecord]:
        return self.list(S.SHADOW)

    def trading(self) -> list[StrategyRecord]:
        """Las que operan con capital (``active`` y ``probation``)."""
        return self.list(S.ACTIVE, S.PROBATION)

    def history(self, name: str) -> list[StrategyTransition]:
        """Historial completo de la estrategia, del más viejo al más reciente."""
        return self.backend.history(self.get(name))

    # --- escritura ---------------------------------------------------------------------
    def register(self, name: str, params: Mapping[str, Any] | None = None) -> StrategyRecord:
        """Da de alta una estrategia en ``research``."""
        return self.backend.create(_validate_name(name), dict(params or {}))

    def ensure(self, name: str, params: Mapping[str, Any] | None = None) -> StrategyRecord:
        """Devuelve la estrategia, registrándola en ``research`` si todavía no existe."""
        return self.find(name) or self.register(name, params)

    def transition(
        self, name: str, to: StrategyState, actor: Actor, reason: str
    ) -> StrategyTransition:
        """Mueve la estrategia a ``to`` y guarda la transición en el historial.

        Args:
            name: estrategia.
            to: estado de destino.
            actor: ``SYSTEM_ACTOR`` para cambios automáticos o el ``user_id`` (UUID) de la
                persona. Las promociones a ``active`` exigen una persona.
            reason: por qué (se guarda en el historial).

        Raises:
            StrategyNotFoundError: si no existe.
            TransitionError: si la transición no está permitida o el actor no puede hacerla.
            StaleStateError: si el estado cambió mientras tanto.
        """
        record = self.get(name)
        who, why = validate_transition(record.state, S(to), actor, reason)
        return self.backend.append_transition(record, S(to), who, why)

    def set_params(self, name: str, params: Mapping[str, Any]) -> StrategyRecord:
        return self.backend.update(name, {"params": dict(params)})

    def record_lab_report(self, name: str, report: Mapping[str, Any]) -> StrategyRecord:
        """Guarda el último reporte del laboratorio (no cambia el estado)."""
        return self.backend.update(name, {"lab_report": dict(report)})

    def record_live_metrics(self, name: str, metrics: Mapping[str, Any]) -> StrategyRecord:
        """Guarda las métricas en vivo (no cambia el estado)."""
        return self.backend.update(name, {"live_metrics": dict(metrics)})
