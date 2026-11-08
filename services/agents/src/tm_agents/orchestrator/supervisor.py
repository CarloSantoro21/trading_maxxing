"""BotSupervisor: un BotRunner por bot en ``running``, con los bots leídos de Supabase.

- ``sync()`` concilia con la tabla ``bots``: arranca un runner por cada bot en ``running`` que
  no tenga y detiene (sin cerrar posiciones) los que ya no estén en ``running``. La API
  escribe el estado antes de publicar el comando, así que la base es la fuente de verdad si
  un comando se pierde.
- ``handle_command()`` aplica ``start``, ``stop`` y ``kill`` de ``tm:commands``. ``kill``
  cierra todas las posiciones del bot y lo detiene.
- ``on_event()`` reparte cada ``SignalEvent`` a todos los runners.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

import httpx
from tm_shared.contracts import DecisionEnvelope, Horizon

from tm_agents.orchestrator.bot import (
    BotCommand,
    BotConfig,
    BotProfile,
    BotRunner,
    BotStatus,
    CommandType,
)
from tm_agents.orchestrator.streams import (
    COMMANDS_STREAM,
    DecisionSink,
    StreamReader,
    decode_fields,
)
from tm_agents.signals import SignalEvent

log = logging.getLogger(__name__)

BOT_SELECT = (
    "id,name,status,strategy_config,"
    "profile:profiles(risk_level,capital,max_drawdown,horizon,instruments)"
)


class BotRepository(Protocol):
    def running(self) -> list[BotConfig]: ...

    def get(self, bot_id: str) -> BotConfig | None: ...


class InMemoryBotRepository:
    """Repositorio en memoria (backtest y pruebas)."""

    def __init__(self, bots: Iterable[BotConfig] = ()) -> None:
        self.bots: dict[str, BotConfig] = {b.bot_id: b for b in bots}

    def running(self) -> list[BotConfig]:
        return [b for b in self.bots.values() if b.status is BotStatus.RUNNING]

    def get(self, bot_id: str) -> BotConfig | None:
        return self.bots.get(bot_id)


def bot_from_row(row: Mapping[str, Any]) -> BotConfig:
    """Fila de PostgREST (``bots`` con su ``profile`` embebido) -> ``BotConfig``."""
    profile = row["profile"]
    return BotConfig(
        bot_id=str(row["id"]),
        name=row.get("name") or "",
        status=BotStatus(row["status"]),
        strategy_config=row.get("strategy_config") or {},
        profile=BotProfile(
            risk_level=int(profile["risk_level"]),
            capital=Decimal(str(profile["capital"])),
            max_drawdown=float(profile["max_drawdown"]),
            horizon=Horizon(profile["horizon"]),
            instruments=tuple(profile.get("instruments") or ("BTCUSDT", "ETHUSDT")),
        ),
    )


class PostgRESTBotRepository:
    """Lee ``bots`` y ``profiles`` de Supabase con la llave ``service_role`` (salta RLS)."""

    def __init__(self, url: str, key: str, *, client: httpx.Client | None = None) -> None:
        if not url or not key:
            raise ValueError("faltan SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY")
        self._base = url.rstrip("/") + "/rest/v1/bots"
        self._headers = {"apikey": key}
        if key.startswith("eyJ"):
            self._headers["Authorization"] = f"Bearer {key}"
        self._client = client or httpx.Client(timeout=10.0)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, **kw: Any) -> PostgRESTBotRepository:
        env = os.environ if env is None else env
        return cls(env.get("SUPABASE_URL", ""), env.get("SUPABASE_SERVICE_ROLE_KEY", ""), **kw)

    def _rows(self, params: dict[str, str]) -> list[BotConfig]:
        resp = self._client.get(
            self._base, params={"select": BOT_SELECT, **params}, headers=self._headers
        )
        resp.raise_for_status()
        out: list[BotConfig] = []
        for row in resp.json():
            try:
                out.append(bot_from_row(row))
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("bot %s con perfil inválido: %s", row.get("id"), exc)
        return out

    def running(self) -> list[BotConfig]:
        return self._rows({"status": "eq.running", "order": "created_at.asc"})

    def get(self, bot_id: str) -> BotConfig | None:
        rows = self._rows({"id": f"eq.{bot_id}"})
        return rows[0] if rows else None


RunnerFactory = Callable[[BotConfig], BotRunner]


class BotSupervisor:
    """Mantiene un BotRunner por bot en ``running`` (ver el módulo)."""

    def __init__(
        self,
        repository: BotRepository,
        sink: DecisionSink,
        *,
        shadow_sink: DecisionSink | None = None,
        runner_factory: RunnerFactory | None = None,
    ) -> None:
        self.repository = repository
        self.sink = sink
        self.shadow_sink = shadow_sink
        self._factory = runner_factory or (
            lambda cfg: BotRunner(cfg, self.sink, shadow_sink=self.shadow_sink)
        )
        self.runners: dict[str, BotRunner] = {}

    def sync(self) -> None:
        try:
            running = {b.bot_id: b for b in self.repository.running()}
        except Exception as exc:
            log.warning("no se pudo leer la tabla bots; se conservan los runners: %s", exc)
            return
        for bot_id, cfg in running.items():
            if bot_id not in self.runners:
                self.runners[bot_id] = self._factory(cfg)
                log.info("bot %s arrancado", bot_id)
        for bot_id in list(self.runners):
            if bot_id not in running:
                self.runners.pop(bot_id).stop()
                log.info("bot %s detenido (ya no está en running)", bot_id)

    def on_event(self, event: SignalEvent) -> list[DecisionEnvelope]:
        out: list[DecisionEnvelope] = []
        for bot_id in sorted(self.runners):
            try:
                out.extend(self.runners[bot_id].on_event(event))
            except Exception as exc:  # un bot con error no detiene a los demás
                log.exception("bot %s falló con %s: %s", bot_id, event.id, exc)
        return out

    def handle_command(self, command: BotCommand, now: datetime) -> list[DecisionEnvelope]:
        runner = self.runners.get(command.bot_id)
        if command.type is CommandType.START:
            if runner is None:
                cfg = self.repository.get(command.bot_id)
                if cfg is None:
                    log.warning("start de un bot desconocido: %s", command.bot_id)
                    return []
                runner = self.runners[command.bot_id] = self._factory(cfg)
            runner.start()
            return []
        if runner is None:
            log.info("%s de un bot sin runner: %s", command.type.value, command.bot_id)
            return []
        out = runner.handle_command(command, now)
        if command.type in (CommandType.STOP, CommandType.KILL):
            self.runners.pop(command.bot_id, None)
        return out


class CommandListener:
    """Lee ``tm:commands`` sin bloquear y devuelve los comandos válidos."""

    def __init__(self, client: Any, stream: str = COMMANDS_STREAM, *, last_id: str = "$"):
        self._reader = StreamReader(client, stream, last_id=last_id)

    def poll(self) -> list[BotCommand]:
        out: list[BotCommand] = []
        for entry_id, fields in self._reader.read():
            try:
                out.append(BotCommand.from_fields(decode_fields(fields), entry_id))
            except (KeyError, ValueError) as exc:
                log.warning("comando inválido en %s (%s): %s", COMMANDS_STREAM, entry_id, exc)
        return out


__all__ = [
    "BotRepository",
    "BotSupervisor",
    "CommandListener",
    "InMemoryBotRepository",
    "PostgRESTBotRepository",
    "bot_from_row",
]
