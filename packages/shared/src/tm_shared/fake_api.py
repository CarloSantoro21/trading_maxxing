"""API de Workers simulada en memoria (``/internal/*``) para pruebas, sin red ni wrangler.

Implementa lo que usa el motor, con las mismas respuestas y códigos de error que
``apps/api/src/routes/internal.ts`` (contrato: ``docs/arquitectura/api.md``)::

    fake = FakeWorkerApi(token="t")
    client = fake.client()                      # WorkerApiClient sobre httpx.MockTransport
    fake.add_bot(...); fake.add_command("start", bot_id)

No valida todo lo que valida zod (eso lo prueban las pruebas del Worker); sí exige el header
``X-Service-Token``, rechaza campos desconocidos en los cuerpos que el motor manda y respeta
los conflictos del ciclo de vida (``strategy_exists``, ``stale_state``).
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import httpx

from tm_shared.api_client import WorkerApiClient

BASE_URL = "https://tm-api.test"

_TRADE_FIELDS = {
    "bot_id",
    "symbol",
    "side",
    "quantity",
    "entry_price",
    "stop_loss",
    "take_profit",
    "fees",
    "rationale",
    "opened_at",
}
_TRADE_PATCH = {
    "quantity",
    "entry_price",
    "fees",
    "rationale",
    "exit_price",
    "closed_at",
    "pnl",
    "stop_loss",
    "take_profit",
}
_NUMERIC = {"quantity", "entry_price", "stop_loss", "take_profit", "fees", "exit_price", "pnl"}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json(status: int, body: Any = None) -> httpx.Response:
    return httpx.Response(status, json=body) if body is not None else httpx.Response(status)


def _err(status: int, detail: str, code: str | None = None) -> httpx.Response:
    body: dict[str, Any] = {"detail": detail}
    if code:
        body["code"] = code
    return httpx.Response(status, json=body)


class FakeWorkerApi:
    """Estado en memoria de D1 y un ``handler`` para ``httpx.MockTransport``."""

    def __init__(self, token: str = "test-token", base_url: str = BASE_URL) -> None:
        self.token = token
        self.base_url = base_url
        self.strategies: dict[str, dict[str, Any]] = {}
        self.transitions: list[dict[str, Any]] = []
        self.bots: dict[str, dict[str, Any]] = {}
        self.trades: dict[str, dict[str, Any]] = {}
        self.reference_trades: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.reference_equity: dict[tuple[str, str], dict[str, Any]] = {}
        self.decisions: list[dict[str, Any]] = []
        self.commands: list[dict[str, Any]] = []
        self.backtests: dict[str, dict[str, Any]] = {}
        self.heartbeats: dict[str, dict[str, Any]] = {}
        self.settings: dict[str, Any] = {"news_enabled": False, "explainer_enabled": False}
        self.state: dict[str, dict[str, Any]] = {}
        self.requests: list[httpx.Request] = []
        #: Respuestas forzadas: ``(método, ruta)`` -> lista de respuestas a devolver primero.
        self.fail: dict[tuple[str, str], list[httpx.Response]] = {}
        self._seq = 0
        self._transition_id = 0

    # --- armado ------------------------------------------------------------------------
    def client(self, **kw: Any) -> WorkerApiClient:
        kw.setdefault("sleep", lambda _s: None)
        return WorkerApiClient(
            self.base_url,
            self.token,
            client=httpx.Client(transport=httpx.MockTransport(self.handler)),
            **kw,
        )

    def add_strategy(self, name: str, state: str = "research", **fields: Any) -> dict[str, Any]:
        now = _now()
        row = {
            "id": str(uuid.uuid4()),
            "name": name,
            "state": state,
            "params": {},
            "lab_report": None,
            "live_metrics": {},
            "state_changed_at": now,
            "created_at": now,
            "updated_at": now,
            "shadow_days_clean": None,
            **fields,
        }
        self.strategies[name] = row
        return row

    def add_bot(
        self,
        bot_id: str | None = None,
        *,
        status: str = "running",
        name: str = "bot",
        strategy_config: Mapping[str, Any] | None = None,
        profile: Mapping[str, Any] | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        bot_id = bot_id or str(uuid.uuid4())
        row = {
            "id": bot_id,
            "name": name,
            "status": status,
            "strategy_config": dict(strategy_config or {}),
            "profile_id": str(uuid.uuid4()),
            "user_id": user_id or str(uuid.uuid4()),
            "profile": {
                "risk_level": 3,
                "capital": 10000,
                "max_drawdown": 0.2,
                "horizon": "medium",
                "instruments": ["BTCUSDT", "ETHUSDT"],
                **dict(profile or {}),
            },
        }
        self.bots[bot_id] = row
        return row

    def add_command(
        self, type_: str, bot_id: str, *, reason: str = "", payload: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        self._seq += 1
        row = {
            "seq": self._seq,
            "id": str(uuid.uuid4()),
            "type": type_,
            "bot_id": bot_id,
            "user_id": self.bots.get(bot_id, {}).get("user_id") or str(uuid.uuid4()),
            "reason": reason,
            "payload": dict(payload or {}),
            "issued_at": _now(),
            "acked_at": None,
            "ack_status": None,
            "ack_error": None,
        }
        self.commands.append(row)
        return row

    def add_backtest(self, config: Mapping[str, Any], *, status: str = "queued") -> dict[str, Any]:
        now = _now()
        row = {
            "id": str(uuid.uuid4()),
            "profile_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "status": status,
            "config": dict(config),
            "report": None,
            "error": None,
            "started_at": None,
            "finished_at": None,
            "created_at": now,
            "updated_at": now,
        }
        self.backtests[row["id"]] = row
        return row

    def calls(self, method: str, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and r.url.path == path]

    # --- despacho ----------------------------------------------------------------------
    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        forced = self.fail.get((request.method, path))
        if forced:
            return forced.pop(0)
        if request.headers.get("X-Service-Token") != self.token:
            return _err(401, "token de servicio inválido")
        if not path.startswith("/internal/"):
            return _err(404, "ruta no encontrada")
        body: Any = None
        if request.content:
            body = json.loads(request.content)
        parts = path.removeprefix("/internal/").split("/")
        q = request.url.params
        name = f"_{request.method.lower()}_{parts[0].replace('-', '_')}"
        handler = getattr(self, name, None)
        if handler is None:
            return _err(404, "ruta no encontrada")
        return handler(parts[1:], q, body)

    # --- estrategias -------------------------------------------------------------------
    def _get_strategies(self, rest: list[str], q: httpx.QueryParams, _b: Any) -> httpx.Response:
        if not rest:
            states = [s for s in (q.get("state") or "").split(",") if s]
            rows = [r for r in self.strategies.values() if not states or r["state"] in states]
            return _json(200, sorted(rows, key=lambda r: r["name"]))
        row = self.strategies.get(rest[0])
        if row is None:
            return _err(404, "Estrategia no encontrado", "not_found")
        if len(rest) == 1:
            return _json(200, row)
        out = [
            {**t, "strategy": row["name"]}
            for t in self.transitions
            if t["strategy_id"] == row["id"]
        ]
        return _json(200, out)

    def _post_strategies(self, rest: list[str], _q: httpx.QueryParams, body: Any) -> httpx.Response:
        if not rest:
            if set(body) - {"name", "params"}:
                return _err(422, "campos desconocidos")
            if body["name"] in self.strategies:
                return _err(409, f"ya existe la estrategia '{body['name']}'", "strategy_exists")
            return _json(201, self.add_strategy(body["name"], params=body.get("params") or {}))
        row = self.strategies.get(rest[0])
        if row is None:
            return _err(404, "Estrategia no encontrado", "not_found")
        if set(body) - {"from_state", "to_state", "actor", "reason"}:
            return _err(422, "campos desconocidos")
        if row["state"] != body["from_state"]:
            return _err(409, f"'{row['name']}' está en {row['state']}", "stale_state")
        self._transition_id += 1
        now = _now()
        transition = {
            "id": self._transition_id,
            "strategy_id": row["id"],
            "from_state": body["from_state"],
            "to_state": body["to_state"],
            "actor": body["actor"],
            "reason": body["reason"],
            "created_at": now,
        }
        self.transitions.append(transition)
        row.update(state=body["to_state"], state_changed_at=now, updated_at=now)
        return _json(201, {**transition, "strategy": row["name"]})

    def _patch_strategies(
        self, rest: list[str], _q: httpx.QueryParams, body: Any
    ) -> httpx.Response:
        row = self.strategies.get(rest[0])
        if row is None:
            return _err(404, "Estrategia no encontrado", "not_found")
        if not body or set(body) - {"params", "lab_report", "live_metrics"}:
            return _err(422, "campos inválidos")
        row.update(body, updated_at=_now())
        return _json(200, row)

    # --- bots y trades -----------------------------------------------------------------
    def _get_bots(self, rest: list[str], q: httpx.QueryParams, _b: Any) -> httpx.Response:
        if rest:
            row = self.bots.get(rest[0])
            return _json(200, row) if row else _err(404, "Bot no encontrado", "not_found")
        status = q.get("status")
        return _json(200, [b for b in self.bots.values() if not status or b["status"] == status])

    def _get_trades(self, _r: list[str], q: httpx.QueryParams, _b: Any) -> httpx.Response:
        rows = list(self.trades.values())
        if q.get("bot_id"):
            rows = [r for r in rows if r["bot_id"] == q["bot_id"]]
        if q.get("open") == "true":
            rows = [r for r in rows if r["closed_at"] is None]
        if q.get("open") == "false":
            rows = [r for r in rows if r["closed_at"] is not None]
        if q.get("side"):
            rows = [r for r in rows if r["side"] == q["side"]]
        rows.sort(key=lambda r: (r["opened_at"], r["id"]))
        limit = int(q.get("limit") or 1000)
        offset = int(q.get("offset") or 0)
        return _json(200, rows[offset : offset + limit])

    def _post_trades(self, _r: list[str], _q: httpx.QueryParams, body: Any) -> httpx.Response:
        if set(body) - _TRADE_FIELDS:
            return _err(422, f"campos desconocidos: {sorted(set(body) - _TRADE_FIELDS)}")
        if not isinstance(body.get("rationale", {}), dict):
            return _err(422, "rationale debe ser objeto")
        now = _now()
        row = {
            "id": str(uuid.uuid4()),
            "stop_loss": None,
            "take_profit": None,
            "fees": 0,
            "rationale": {},
            "exit_price": None,
            "pnl": None,
            "closed_at": None,
            "opened_at": now,
            **body,
        }
        for key in _NUMERIC:
            if row.get(key) is not None:
                row[key] = float(row[key])
        self.trades[row["id"]] = row
        return _json(201, row)

    def _patch_trades(self, rest: list[str], _q: httpx.QueryParams, body: Any) -> httpx.Response:
        row = self.trades.get(rest[0])
        if row is None:
            return _err(404, "Trade no encontrado", "not_found")
        if not body or set(body) - _TRADE_PATCH:
            return _err(422, "campos inválidos")
        for key, value in body.items():
            row[key] = float(value) if key in _NUMERIC and value is not None else value
        return _json(200, row)

    def _post_equity_snapshots(self, _r: list[str], _q: Any, body: Any) -> httpx.Response:
        return _json(200, {"upserted": len(body["snapshots"])})

    # --- ledger de referencia ----------------------------------------------------------
    def _post_reference(self, rest: list[str], _q: Any, body: Any) -> httpx.Response:
        if rest != ["trades"] or set(body) != {"rows"}:
            return _err(422, "cuerpo inválido")
        inserted = 0
        for r in body["rows"]:
            key = (r["strategy_id"], r["symbol"], r["closed_at"])
            if key not in self.reference_trades:
                self.reference_trades[key] = dict(r)
                inserted += 1
        return _json(200, {"received": len(body["rows"]), "inserted": inserted})

    def _put_reference(self, rest: list[str], _q: Any, body: Any) -> httpx.Response:
        if rest != ["equity"] or set(body) != {"points"}:
            return _err(422, "cuerpo inválido")
        for p in body["points"]:
            self.reference_equity[(p["strategy_id"], p["day"])] = dict(p)
        return _json(200, {"upserted": len(body["points"])})

    def _get_reference(self, rest: list[str], q: httpx.QueryParams, _b: Any) -> httpx.Response:
        sid = q.get("strategy_id")
        rows = [p for (s, _d), p in sorted(self.reference_equity.items()) if s == sid]
        return _json(200, rows)

    # --- decisiones, estado, comandos, backtests, latido y ajustes ----------------------
    def _post_decisions(self, _r: list[str], _q: Any, body: Any) -> httpx.Response:
        allowed = {"bot_id", "strategy", "symbol", "kind", "payload", "decided_at"}
        for d in body["decisions"]:
            if set(d) - allowed or not d.get("kind"):
                return _err(422, "decisión inválida")
            self.decisions.append({**d, "decided_at": d.get("decided_at") or _now()})
        return _json(201, {"inserted": len(body["decisions"])})

    def _get_state(self, rest: list[str], _q: Any, _b: Any) -> httpx.Response:
        row = self.state.get(rest[0])
        return _json(200, row) if row else _err(404, "Estado no encontrado", "not_found")

    def _put_state(self, rest: list[str], _q: Any, body: Any) -> httpx.Response:
        row = {"key": rest[0], "value": body.get("value"), "updated_at": _now()}
        self.state[rest[0]] = row
        return _json(200, row)

    def _get_commands(self, _r: list[str], q: httpx.QueryParams, _b: Any) -> httpx.Response:
        after = int(q.get("after") or 0)
        limit = int(q.get("limit") or 100)
        keys = ("seq", "id", "type", "bot_id", "user_id", "reason", "payload", "issued_at")
        rows = [
            {k: c[k] for k in keys}
            for c in self.commands
            if c["acked_at"] is None and c["seq"] > after
        ]
        return _json(200, rows[:limit])

    def _post_commands(self, rest: list[str], _q: Any, body: Any) -> httpx.Response:
        if len(rest) != 2 or rest[1] != "ack":
            return _err(404, "ruta no encontrada")
        for c in self.commands:
            if c["id"] == rest[0]:
                body = body or {}
                c.update(
                    acked_at=_now(),
                    ack_status=body.get("status", "done"),
                    ack_error=body.get("error"),
                )
                keys = ("seq", "id", "type", "bot_id", "acked_at", "ack_status", "ack_error")
                return _json(200, {k: c[k] for k in keys})
        return _err(404, "Comando no encontrado", "not_found")

    def _get_backtests(self, _r: list[str], q: httpx.QueryParams, _b: Any) -> httpx.Response:
        status = q.get("status")
        rows = [b for b in self.backtests.values() if not status or b["status"] == status]
        return _json(200, rows[: int(q.get("limit") or 20)])

    def _patch_backtests(self, rest: list[str], _q: Any, body: Any) -> httpx.Response:
        row = self.backtests.get(rest[0])
        if row is None:
            return _err(404, "Backtest no encontrado", "not_found")
        allowed = {"status", "report", "error", "started_at", "finished_at"}
        if not body or set(body) - allowed:
            return _err(422, "campos inválidos")
        merged = {**row, **body}
        if merged["status"] == "done" and merged["report"] is None:
            return _err(422, "done sin reporte", "check_violation")
        if merged["status"] == "failed" and not merged["error"]:
            return _err(422, "failed sin error", "check_violation")
        row.update(body, updated_at=_now())
        return _json(200, row)

    def _post_heartbeat(self, _r: list[str], _q: Any, body: Any) -> httpx.Response:
        allowed = {"component", "status", "version", "host", "started_at", "details"}
        if set(body) - allowed or not re.fullmatch(r"[a-z0-9_-]{1,64}", body["component"]):
            return _err(422, "latido inválido")
        row = {"status": "ok", "details": {}, **body, "last_seen_at": _now()}
        self.heartbeats[body["component"]] = row
        return _json(200, row)

    def _get_settings(self, _r: list[str], _q: Any, _b: Any) -> httpx.Response:
        return _json(200, dict(self.settings))


__all__ = ["BASE_URL", "FakeWorkerApi"]
