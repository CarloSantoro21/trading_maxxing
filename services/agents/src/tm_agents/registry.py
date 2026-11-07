"""StrategyRegistry: estado del ciclo de vida de cada estrategia (propuesta, sección 3.3).

El código de una estrategia entra por PR; su estado es un dato que vive en la tabla
``strategies`` de Supabase, con su historial en ``strategy_transitions``::

    research → lab → shadow → active → probation → retired
                 ▲                                    │
                 └──────────── revalidación ──────────┘

Reglas (iguales a las de la migración ``20261108000100_strategies.sql``):

- Solo existen las transiciones de :data:`ALLOWED_TRANSITIONS`.
- Las promociones (``shadow → active`` y ``probation → active``) solo las hace una persona:
  el actor es el ``user_id`` (UUID) de quien aprueba, nunca ``"system"``.
- Las degradaciones y los retiros pueden ser automáticos (``"system"``) o manuales.
- Cada cambio de estado queda en el historial con quién, cuándo y por qué.

Hay dos backends: :class:`InMemoryBackend` (pruebas y backtests) y
:class:`PostgRESTBackend` (Supabase con la llave ``service_role``)::

    from tm_agents.registry import SYSTEM_ACTOR, PostgRESTBackend, StrategyRegistry
    from tm_agents.strategy_stats import StrategyState

    registry = StrategyRegistry(PostgRESTBackend.from_env())
    registry.transition("trend", StrategyState.PROBATION, SYSTEM_ACTOR, "drawdown > p95")
    registry.history("trend")
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Protocol
from uuid import UUID, uuid4

import httpx

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
    """Error genérico del registro (por ejemplo, una respuesta inesperada de PostgREST)."""


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
        """Construye el registro a partir de una fila de PostgREST."""
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
    # PostgREST devuelve ISO 8601; Python 3.12 entiende el "+00:00" y la "Z".
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


class PostgRESTBackend:
    """Backend sobre la API REST de Supabase (PostgREST) con la llave ``service_role``.

    La base vuelve a validar todo (checks y triggers de la migración), así que aunque otro
    servicio escriba directo, el ciclo de vida se respeta. Cada transición es un solo
    ``INSERT`` en ``strategy_transitions``: el trigger bloquea la fila de la estrategia,
    exige que ``from_state`` sea el estado actual y actualiza ``strategies.state`` en la
    misma transacción.

    Args:
        url: URL base de Supabase (``SUPABASE_URL``), p. ej. ``http://127.0.0.1:54321``.
        key: llave ``service_role`` (JWT) o ``sb_secret_...``. Nunca la del frontend.
        client: cliente de httpx opcional (para pruebas o para reutilizar conexiones).
        timeout: segundos por petición.
    """

    def __init__(
        self,
        url: str,
        key: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 10.0,
    ) -> None:
        if not url:
            raise ValueError("falta la URL de Supabase")
        if not key:
            raise ValueError("falta la llave service_role de Supabase")
        headers = {"apikey": key, "Content-Type": "application/json"}
        # Las llaves nuevas (sb_secret_...) no son JWT y solo van en el header apikey.
        if key.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {key}"
        self._base = url.rstrip("/") + "/rest/v1"
        self._client = client or httpx.Client(timeout=timeout)
        self._headers = headers

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, **kwargs: Any) -> PostgRESTBackend:
        """Lee ``SUPABASE_URL`` y ``SUPABASE_SERVICE_ROLE_KEY`` del entorno."""
        env = os.environ if env is None else env
        return cls(env.get("SUPABASE_URL", ""), env.get("SUPABASE_SERVICE_ROLE_KEY", ""), **kwargs)

    def close(self) -> None:
        self._client.close()

    # --- lectura -----------------------------------------------------------------------
    def get(self, name: str) -> StrategyRecord | None:
        rows = self._request("GET", "/strategies", params={"name": f"eq.{name}", "select": "*"})
        return StrategyRecord.from_row(rows[0]) if rows else None

    def list(self, states: Iterable[StrategyState] | None = None) -> list[StrategyRecord]:
        params = {"select": "*", "order": "name.asc"}
        if states is not None:
            params["state"] = "in.(" + ",".join(S(s).value for s in states) + ")"
        return [StrategyRecord.from_row(r) for r in self._request("GET", "/strategies", params)]

    def history(self, record: StrategyRecord) -> list[StrategyTransition]:
        rows = self._request(
            "GET",
            "/strategy_transitions",
            params={
                "strategy_id": f"eq.{record.id}",
                "select": "*",
                "order": "created_at.asc,id.asc",
            },
        )
        return [StrategyTransition.from_row(record.name, r) for r in rows]

    # --- escritura ---------------------------------------------------------------------
    def create(self, name: str, params: Mapping[str, Any]) -> StrategyRecord:
        rows = self._request(
            "POST", "/strategies", json={"name": name, "params": dict(params)}, returning=True
        )
        return StrategyRecord.from_row(rows[0])

    def update(self, name: str, fields: Mapping[str, Any]) -> StrategyRecord:
        rows = self._request(
            "PATCH",
            "/strategies",
            params={"name": f"eq.{name}"},
            json=dict(fields),
            returning=True,
        )
        if not rows:
            raise StrategyNotFoundError(name)
        return StrategyRecord.from_row(rows[0])

    def append_transition(
        self, record: StrategyRecord, to_state: StrategyState, actor: str, reason: str
    ) -> StrategyTransition:
        rows = self._request(
            "POST",
            "/strategy_transitions",
            json={
                "strategy_id": str(record.id),
                "from_state": record.state.value,
                "to_state": S(to_state).value,
                "actor": actor,
                "reason": reason,
            },
            returning=True,
        )
        return StrategyTransition.from_row(record.name, rows[0])

    # --- HTTP --------------------------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        params: Mapping[str, str] | None = None,
        *,
        json: Any = None,
        returning: bool = False,
    ) -> list[dict[str, Any]]:
        headers = dict(self._headers)
        if returning:
            headers["Prefer"] = "return=representation"
        try:
            resp = self._client.request(
                method, self._base + path, params=params, json=json, headers=headers
            )
        except httpx.HTTPError as exc:
            raise RegistryError(f"no se pudo llamar a PostgREST: {exc}") from exc
        if resp.is_success:
            return resp.json() if resp.content else []
        raise _error_from_response(resp)


def _error_from_response(resp: httpx.Response) -> RegistryError:
    """Traduce los errores de Postgres que devuelve PostgREST a errores del registro."""
    try:
        body = resp.json()
    except ValueError:
        body = {}
    code = body.get("code", "") if isinstance(body, dict) else ""
    message = body.get("message", resp.text) if isinstance(body, dict) else resp.text
    if code == "40001":
        return StaleStateError(message)
    if code == "23505":
        return StrategyExistsError(message)
    if code in ("23514", "42501"):
        return TransitionError(message)
    if code == "23503":
        return StrategyNotFoundError(message)
    return RegistryError(f"PostgREST respondió {resp.status_code}: {message}")


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
