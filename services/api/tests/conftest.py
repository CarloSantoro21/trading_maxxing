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


# Valores por defecto de cada tabla (los que pondría PostgreSQL).
_DEFAULTS: dict[str, Callable[[], Row]] = {
    "profiles": lambda: {"instruments": ["BTCUSDT", "ETHUSDT"]},
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
}
# Tablas que el rol authenticated puede escribir (según las migraciones de RLS).
_USER_INSERT = {"profiles", "goals", "bots", "backtests"}
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

    def _visible(self, table: str, uid: str, row: Row) -> bool:
        if table == "profiles":
            return str(row["user_id"]) == uid
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
        if not self._visible(table, uid, full):
            raise _forbidden()
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
