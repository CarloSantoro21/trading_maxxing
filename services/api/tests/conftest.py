"""Fixtures del servicio api: JWT de prueba, una base en memoria que imita PostgREST + RLS
y Redis simulado con fakeredis. Nada toca la red."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import fakeredis
import jwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from tm_api.commands import CommandBus
from tm_api.main import create_app
from tm_api.settings import Settings

SECRET = "super-secret-jwt-token-with-at-least-32-characters-long"
ALICE = uuid.UUID("11111111-1111-4111-8111-111111111111")
BOB = uuid.UUID("22222222-2222-4222-8222-222222222222")

Row = dict[str, Any]


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "integration: requiere Supabase local (SUPABASE_URL); se omite en CI"
    )


def make_token(
    sub: uuid.UUID | str = ALICE,
    *,
    secret: str = SECRET,
    exp_in: int = 3600,
    role: str = "authenticated",
    aud: str = "authenticated",
    **extra: Any,
) -> str:
    now = int(time.time())
    claims = {"sub": str(sub), "role": role, "aud": aud, "iat": now, "exp": now + exp_in, **extra}
    return jwt.encode(claims, secret, algorithm="HS256")


def auth(sub: uuid.UUID = ALICE) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(sub, email=f'{sub}@test.local')}"}


# Drawdown máximo por nivel: lo que hace el trigger profiles_derive_max_drawdown.
RISK_LEVEL_MAX_DRAWDOWN = {1: 0.10, 2: 0.15, 3: 0.20, 4: 0.30, 5: 0.40}
# Transiciones del ciclo de vida que acepta el trigger de strategy_transitions.
_ALLOWED_TRANSITIONS = {
    "research": {"lab", "retired"},
    "lab": {"shadow", "research", "retired"},
    "shadow": {"active", "lab", "retired"},
    "active": {"probation", "retired"},
    "probation": {"active", "retired"},
    "retired": {"lab"},
}

# Valores por defecto de cada tabla (los que pondría PostgreSQL).
_DEFAULTS: dict[str, Callable[[], Row]] = {
    "profiles": lambda: {"horizon": "medium", "instruments": ["BTCUSDT", "ETHUSDT"]},
    "goals": dict,
    "bots": lambda: {
        "status": "stopped",
        "strategy_config": {},
        "started_at": None,
        "stopped_at": None,
    },
    "backtests": lambda: {
        "status": "queued",
        "report": None,
        "error": None,
        "started_at": None,
        "finished_at": None,
    },
    "trades": lambda: {
        "exit_price": None,
        "stop_loss": None,
        "take_profit": None,
        "fees": 0,
        "pnl": None,
        "rationale": {},
        "closed_at": None,
    },
    "equity_snapshots": lambda: {"positions_value": 0, "drawdown": 0},
    "app_roles": lambda: {"role": "member"},
    "strategies": lambda: {
        "state": "research",
        "params": {},
        "lab_report": None,
        "live_metrics": {},
        "state_changed_at": datetime.now(UTC).isoformat(),
    },
    "strategy_transitions": dict,
    "reference_equity": lambda: {"cash": 0, "positions_value": 0, "drawdown": 0},
}
# Tablas que el rol authenticated puede escribir (según las migraciones de RLS).
_USER_INSERT = {"profiles", "goals", "bots", "backtests", "strategy_transitions"}
_USER_UPDATE = {"profiles", "goals", "bots"}
_USER_DELETE = {"profiles", "goals", "bots", "backtests"}
_CHILDREN = {
    "profiles": [("goals", "profile_id"), ("bots", "profile_id"), ("backtests", "profile_id")],
    "bots": [("trades", "bot_id"), ("equity_snapshots", "bot_id")],
}


def _forbidden() -> HTTPException:
    return HTTPException(status_code=403, detail={"message": "RLS", "code": "42501"})


class FakeDatabase:
    """Doble de `PostgrestClient` en memoria que aplica las mismas reglas de RLS."""

    def __init__(self) -> None:
        self.tables: dict[str, list[Row]] = {name: [] for name in _DEFAULTS}
        self.calls: list[tuple[str, str]] = []

    # --- RLS -----------------------------------------------------------------
    @staticmethod
    def _uid(token: str) -> str:
        return str(jwt.decode(token, options={"verify_signature": False})["sub"])

    def _owns_profile(self, uid: str, profile_id: Any) -> bool:
        return any(
            str(p["id"]) == str(profile_id) and str(p["user_id"]) == uid
            for p in self.tables["profiles"]
        )

    def _owns_bot(self, uid: str, bot_id: Any) -> bool:
        return any(
            str(b["id"]) == str(bot_id) and self._owns_profile(uid, b["profile_id"])
            for b in self.tables["bots"]
        )

    def _is_admin(self, uid: str) -> bool:
        return any(
            str(r["user_id"]) == uid and r["role"] == "admin" for r in self.tables["app_roles"]
        )

    def _visible(self, table: str, uid: str, row: Row) -> bool:
        if table in ("profiles", "app_roles"):
            return str(row["user_id"]) == uid
        if table in ("strategies", "strategy_transitions", "reference_equity"):
            return True
        if table in ("goals", "bots", "backtests"):
            return self._owns_profile(uid, row["profile_id"])
        return self._owns_bot(uid, row["bot_id"])

    # --- filtros de PostgREST ------------------------------------------------
    @staticmethod
    def _match(row: Row, key: str, expr: str) -> bool:
        value = row.get(key)
        if expr == "is.null":
            return value is None
        if expr == "not.is.null":
            return value is not None
        op, _, arg = expr.partition(".")
        if op == "eq":
            return str(value) == arg
        if op == "neq":
            return str(value) != arg
        if op == "in":
            return str(value) in arg.strip("()").split(",")
        raise AssertionError(f"filtro no soportado en el doble: {key}={expr}")

    def _query(self, table: str, token: str, params: dict[str, str]) -> list[Row]:
        uid = self._uid(token)
        reserved = {"order", "limit", "offset", "select"}
        rows = [
            r
            for r in self.tables[table]
            if self._visible(table, uid, r)
            and all(self._match(r, k, v) for k, v in params.items() if k not in reserved)
        ]
        for part in reversed(params.get("order", "").split(",")):
            if part:
                col, _, direction = part.partition(".")
                rows.sort(key=lambda r, c=col: str(r.get(c) or ""), reverse=direction == "desc")
        return rows

    @staticmethod
    def _project(rows: list[Row], params: dict[str, str]) -> list[Row]:
        if "select" not in params:
            return [dict(r) for r in rows]
        cols = params["select"].split(",")
        return [{c: r.get(c) for c in cols} for r in rows]

    # --- interfaz Database ---------------------------------------------------
    async def select(
        self, table: str, token: str, params: dict[str, str] | None = None
    ) -> list[Row]:
        params = params or {}
        self.calls.append(("select", table))
        rows = self._query(table, token, params)
        offset = int(params.get("offset", 0))
        limit = int(params["limit"]) if "limit" in params else None
        rows = rows[offset : offset + limit if limit is not None else None]
        return self._project(rows, params)

    async def select_page(
        self, table: str, token: str, params: dict[str, str], *, limit: int, offset: int
    ) -> tuple[list[Row], int]:
        self.calls.append(("select_page", table))
        rows = self._query(table, token, params)
        return self._project(rows[offset : offset + limit], params), len(rows)

    async def insert(self, table: str, token: str, row: Row) -> Row:
        self.calls.append(("insert", table))
        uid = self._uid(token)
        if table not in _USER_INSERT:
            raise _forbidden()
        now = datetime.now(UTC).isoformat()
        full = {
            **_DEFAULTS[table](),
            **row,
            "id": str(uuid.uuid4()),
            "created_at": now,
            "updated_at": now,
        }
        if table == "strategy_transitions":
            return self._apply_transition(uid, full)
        if not self._visible(table, uid, full):
            raise _forbidden()
        if table == "profiles":
            self._derive_profile(full)
        if table == "profiles" and any(
            str(p["user_id"]) == str(full["user_id"]) for p in self.tables["profiles"]
        ):
            raise HTTPException(status_code=409, detail={"message": "dup", "code": "23505"})
        self.tables[table].append(full)
        return dict(full)

    async def update(
        self, table: str, token: str, filters: dict[str, str], values: Row
    ) -> list[Row]:
        self.calls.append(("update", table))
        if table not in _USER_UPDATE:
            return []
        rows = self._query(table, token, filters)
        for r in rows:
            r.update(values, updated_at=datetime.now(UTC).isoformat())
            if table == "profiles":
                self._derive_profile(r)
        return [dict(r) for r in rows]

    async def delete(self, table: str, token: str, filters: dict[str, str]) -> list[Row]:
        self.calls.append(("delete", table))
        if table not in _USER_DELETE:
            return []
        rows = self._query(table, token, filters)
        for r in rows:
            self._cascade(table, r)
            self.tables[table].remove(r)
        return [dict(r) for r in rows]

    # --- triggers y checks de la base --------------------------------------------
    @staticmethod
    def _check_violation(message: str) -> HTTPException:
        return HTTPException(status_code=422, detail={"message": message, "code": "23514"})

    def _derive_profile(self, row: Row) -> None:
        """Trigger profiles_derive_max_drawdown y check profiles_capital_min."""
        if row.get("risk_level") not in RISK_LEVEL_MAX_DRAWDOWN:
            raise self._check_violation("risk_level")
        if float(row["capital"]) < 500:
            raise self._check_violation("profiles_capital_min")
        row["max_drawdown"] = RISK_LEVEL_MAX_DRAWDOWN[row["risk_level"]]

    def _apply_transition(self, uid: str, row: Row) -> Row:
        """Política strategy_transitions_insert_admin + trigger apply_strategy_transition."""
        strategy = next(
            (s for s in self.tables["strategies"] if str(s["id"]) == str(row["strategy_id"])),
            None,
        )
        if strategy is None:
            raise HTTPException(status_code=422, detail={"message": "fk", "code": "23503"})
        if strategy["state"] != row["from_state"]:
            raise HTTPException(status_code=409, detail={"message": "stale", "code": "40001"})
        if row["to_state"] not in _ALLOWED_TRANSITIONS[row["from_state"]]:
            raise self._check_violation("transición no permitida")
        if not self._is_admin(uid) or row["actor"] != uid:
            raise _forbidden()
        strategy["state"] = row["to_state"]
        strategy["state_changed_at"] = row["created_at"]
        self.tables["strategy_transitions"].append(row)
        return dict(row)

    def _cascade(self, table: str, row: Row) -> None:
        for child, fk in _CHILDREN.get(table, []):
            for c in [c for c in self.tables[child] if str(c[fk]) == str(row["id"])]:
                self._cascade(child, c)
                self.tables[child].remove(c)

    # --- siembra directa (como la service role del engine) --------------------
    def seed(self, table: str, **values: Any) -> Row:
        now = datetime.now(UTC).isoformat()
        row = {**_DEFAULTS[table](), "id": str(uuid.uuid4()), "created_at": now, "updated_at": now}
        row.update(values)
        self.tables[table].append(row)
        return row


@pytest.fixture
def settings() -> Settings:
    return Settings(supabase_jwt_secret=SECRET, supabase_anon_key="anon-key")


@pytest.fixture
def fake_db() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture
def redis_server() -> fakeredis.FakeServer:
    return fakeredis.FakeServer()


@pytest.fixture
def redis_reader(redis_server: fakeredis.FakeServer) -> fakeredis.FakeRedis:
    """Cliente síncrono sobre el mismo servidor simulado, para leer los streams."""
    return fakeredis.FakeRedis(server=redis_server, decode_responses=True)


@pytest.fixture
def client(
    settings: Settings, fake_db: FakeDatabase, redis_server: fakeredis.FakeServer
) -> Iterator[TestClient]:
    bus = CommandBus(fakeredis.FakeAsyncRedis(server=redis_server), settings)
    app = create_app(settings, db=fake_db, bus=bus)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def make_admin(fake_db: FakeDatabase) -> Callable[..., Row]:
    """Da el rol de admin a un usuario (como lo haría `make user ... ROLE=admin`)."""

    def _make(user_id: uuid.UUID = ALICE) -> Row:
        return fake_db.seed("app_roles", user_id=str(user_id), role="admin")

    return _make


@pytest.fixture
def profile(fake_db: FakeDatabase) -> Row:
    return fake_db.seed(
        "profiles",
        user_id=str(ALICE),
        horizon="medium",
        risk_level=3,
        capital=10000,
        max_drawdown=0.2,
    )


@pytest.fixture
def bot(fake_db: FakeDatabase, profile: Row) -> Row:
    return fake_db.seed("bots", profile_id=profile["id"], name="trend-btc")


# Con --import-mode=importlib las pruebas no pueden importar conftest; los helpers
# se exponen como fixtures.
@pytest.fixture
def token() -> Callable[..., str]:
    return make_token


@pytest.fixture
def alice() -> dict[str, str]:
    return auth(ALICE)


@pytest.fixture
def bob() -> dict[str, str]:
    return auth(BOB)
