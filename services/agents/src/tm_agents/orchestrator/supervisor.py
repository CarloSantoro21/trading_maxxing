"""BotSupervisor: un BotRunner por bot en ``running``, con los bots leídos de la API (D1).

- ``sync()`` concilia con la tabla ``bots`` (``GET /internal/bots?status=running``): arranca
  un runner por cada bot en ``running`` que no tenga y detiene (sin cerrar posiciones) los que
  ya no estén en ``running``. La API escribe el estado y el comando en el mismo batch, así que
  la base es la fuente de verdad si un comando se pierde.
- ``handle_command()`` aplica ``start``, ``stop`` y ``kill`` de la cola ``commands``
  (:class:`WorkerApiCommandListener`: ``GET /internal/commands`` y ``ack``). ``kill`` cierra
  todas las posiciones del bot y lo detiene.
- ``on_event()`` reparte cada ``SignalEvent`` a todos los runners.

Con un ``trades`` (:class:`~tm_agents.orchestrator.state.TradeStateRepository`), cada runner
nuevo (al arrancar el worker, en ``sync`` o con ``start``) reconstruye su libro desde la tabla
``trades`` antes de operar (#89). Si no se puede leer, el runner no arranca y se reintenta en
el siguiente ``sync``: un libro vacío con posiciones abiertas en el motor duplicaría compras.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol
from urllib.parse import quote

from tm_shared.api_client import WorkerApiClient, WorkerApiError
from tm_shared.contracts import DecisionEnvelope, Horizon

from tm_agents.explainer import ExplainerAgent
from tm_agents.orchestrator.bot import (
    BotCommand,
    BotConfig,
    BotProfile,
    BotRunner,
    BotStatus,
    CommandType,
)
from tm_agents.orchestrator.state import TradeStateRepository
from tm_agents.orchestrator.streams import DecisionSink
from tm_agents.signals import SignalEvent

log = logging.getLogger(__name__)


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
    """Fila de ``/internal/bots`` (``bots`` con su ``profile`` embebido) -> ``BotConfig``."""
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


class WorkerApiBotRepository:
    """Lee ``bots`` con su perfil desde ``/internal/bots`` (la API salta el filtro por usuario)."""

    def __init__(self, client: WorkerApiClient) -> None:
        self.client = client

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> WorkerApiBotRepository:
        return cls(WorkerApiClient.from_env(env))

    def _parse(self, rows: Iterable[Mapping[str, Any]]) -> list[BotConfig]:
        out: list[BotConfig] = []
        for row in rows:
            try:
                out.append(bot_from_row(row))
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("bot %s con perfil inválido: %s", row.get("id"), exc)
        return out

    def running(self) -> list[BotConfig]:
        return self._parse(self.client.get("/internal/bots", {"status": "running"}) or [])

    def get(self, bot_id: str) -> BotConfig | None:
        try:
            row = self.client.get(f"/internal/bots/{quote(bot_id)}")
        except WorkerApiError as exc:
            if exc.not_found or exc.status == 422:  # 422: id que no es UUID
                return None
            raise
        rows = self._parse([row])
        return rows[0] if rows else None


RunnerFactory = Callable[[BotConfig], BotRunner]
#: ``(bot_id, envelope_id, texto)``; ver ``tm_agents.explainer_runtime.WorkerApiExplanationSink``.
BotExplanationSink = Callable[[str, str, str], None]


class BotSupervisor:
    """Mantiene un BotRunner por bot en ``running`` (ver el módulo)."""

    def __init__(
        self,
        repository: BotRepository,
        sink: DecisionSink,
        *,
        shadow_sink: DecisionSink | None = None,
        runner_factory: RunnerFactory | None = None,
        trades: TradeStateRepository | None = None,
        explainer: ExplainerAgent | None = None,
        explanation_sink: BotExplanationSink | None = None,
        explain_executor: Callable[[Any], Any] | None = None,
    ) -> None:
        self.repository = repository
        self.sink = sink
        self.shadow_sink = shadow_sink
        self.trades = trades
        #: ExplainerAgent que reciben los runners del factory por omisión (#118: apagado =
        #: plantilla, sin LLM).
        self.explainer = explainer
        #: ``(bot_id, envelope_id, texto)``: dónde se guarda cada explicación (la API).
        self.explanation_sink = explanation_sink
        #: Dónde corre la corrutina del explainer en vivo (``ExplainerLoop``).
        self.explain_executor = explain_executor
        self._factory = runner_factory or self._default_runner
        self.runners: dict[str, BotRunner] = {}

    def _default_runner(self, cfg: BotConfig) -> BotRunner:
        sink = self.explanation_sink
        return BotRunner(
            cfg,
            self.sink,
            shadow_sink=self.shadow_sink,
            explainer=self.explainer,
            explanation_sink=(
                None if sink is None else (lambda eid, text: sink(cfg.bot_id, eid, text))
            ),
            explain_executor=self.explain_executor,
        )

    def _spawn(self, cfg: BotConfig) -> BotRunner | None:
        """Crea el runner del bot y le restaura el libro desde ``trades`` (si hay repositorio)."""
        runner = self._factory(cfg)
        if self.trades is not None:
            try:
                runner.restore(self.trades.load(cfg.bot_id))
            except Exception as exc:
                log.warning("bot %s sin arrancar: no se pudo leer trades (%s)", cfg.bot_id, exc)
                return None
        self.runners[cfg.bot_id] = runner
        return runner

    def sync(self) -> None:
        try:
            running = {b.bot_id: b for b in self.repository.running()}
        except Exception as exc:
            log.warning("no se pudo leer la tabla bots; se conservan los runners: %s", exc)
            return
        for bot_id, cfg in running.items():
            if bot_id not in self.runners and self._spawn(cfg) is not None:
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
                runner = self._spawn(cfg)
                if runner is None:
                    return []
            runner.start()
            return []
        if runner is None:
            log.info("%s de un bot sin runner: %s", command.type.value, command.bot_id)
            return []
        out = runner.handle_command(command, now)
        if command.type in (CommandType.STOP, CommandType.KILL):
            self.runners.pop(command.bot_id, None)
        return out


class CommandSource(Protocol):
    """De dónde salen los comandos de bots (``start``, ``stop``, ``kill``)."""

    def poll(self) -> list[BotCommand]: ...

    def ack(self, command: BotCommand, error: str | None = None) -> None: ...


class InMemoryCommandSource:
    """Cola de comandos en memoria (pruebas): ``push`` y luego ``poll``; guarda los ack."""

    def __init__(self) -> None:
        self.pending: list[BotCommand] = []
        self.acked: list[tuple[BotCommand, str | None]] = []

    def push(self, command: BotCommand) -> None:
        self.pending.append(command)

    def poll(self) -> list[BotCommand]:
        out, self.pending = self.pending, []
        return out

    def ack(self, command: BotCommand, error: str | None = None) -> None:
        self.acked.append((command, error))


class WorkerApiCommandListener:
    """Lee la cola ``commands`` de D1 (``GET /internal/commands?after=<seq>``) y la confirma.

    Sustituye al stream ``tm:commands`` que antes publicaba la API de FastAPI. Cada comando
    se confirma con ``POST /internal/commands/{id}/ack`` (``done`` o ``failed`` con el error)
    después de aplicarlo; confirmado ya no vuelve a aparecer. Al arrancar se empieza en
    ``after=0``: los comandos que llegaron mientras la Mac estaba apagada se aplican.

    Se consulta a lo más cada ``interval`` segundos (el plan gratuito de Workers da 100,000
    peticiones al día: cada 5 s son ~17,000). Si un ``ack`` falla se reintenta en el siguiente
    ``poll`` sin volver a aplicar el comando.
    """

    def __init__(
        self,
        client: WorkerApiClient,
        *,
        interval: float = 5.0,
        limit: int = 100,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.interval = interval
        self.limit = limit
        self.last_seq = 0
        self._clock = clock
        self._last_poll = float("-inf")
        self._unacked: dict[str, str | None] = {}

    def poll(self) -> list[BotCommand]:
        now = self._clock()
        if now - self._last_poll < self.interval:
            return []
        self._last_poll = now
        self._retry_acks()
        try:
            rows = self.client.get(
                "/internal/commands", {"after": self.last_seq, "limit": self.limit}
            )
        except WorkerApiError as exc:
            log.warning("no se pudieron leer los comandos: %s", exc)
            return []
        out: list[BotCommand] = []
        for row in rows or []:
            self.last_seq = max(self.last_seq, int(row.get("seq") or 0))
            if str(row.get("id")) in self._unacked:
                continue
            try:
                out.append(BotCommand.from_api(row))
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("comando inválido %s: %s", row.get("id"), exc)
                if row.get("id"):
                    self.ack_id(str(row["id"]), f"comando inválido: {exc}")
        return out

    def ack(self, command: BotCommand, error: str | None = None) -> None:
        if command.id:
            self.ack_id(command.id, error)

    def ack_id(self, command_id: str, error: str | None = None) -> None:
        if self._send_ack(command_id, error):
            self._unacked.pop(command_id, None)
        else:
            self._unacked[command_id] = error

    def _send_ack(self, command_id: str, error: str | None) -> bool:
        body: dict[str, Any] = {"status": "failed" if error else "done"}
        if error:
            body["error"] = error[:2000]
        try:
            self.client.post(f"/internal/commands/{quote(command_id)}/ack", body, idempotent=True)
        except WorkerApiError as exc:
            if exc.not_found:
                return True  # ya no existe (bot borrado): nada que confirmar
            log.warning("no se pudo confirmar el comando %s: %s", command_id, exc)
            return False
        return True

    def _retry_acks(self) -> None:
        for command_id, error in list(self._unacked.items()):
            self.ack_id(command_id, error)


__all__ = [
    "BotExplanationSink",
    "BotRepository",
    "BotSupervisor",
    "CommandSource",
    "InMemoryBotRepository",
    "InMemoryCommandSource",
    "WorkerApiBotRepository",
    "WorkerApiCommandListener",
    "bot_from_row",
]
