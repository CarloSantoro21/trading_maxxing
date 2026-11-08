"""Ejecución de las dos capas: en vivo (worker con Redis) y en backtest (todo en memoria).

En vivo, el worker ``agents`` corre las dos capas en el mismo proceso, comunicadas por Redis:

1. al cerrar cada minuto, la capa compartida evalúa las estrategias cuyo timeframe cerró y
   publica en ``tm:signals``;
2. el supervisor lee ``tm:signals`` (como cualquier otro consumidor) y ``tm:commands`` y
   publica en ``tm:decisions`` y ``tm:shadow``;
3. cada ``sync_every`` segundos concilia los bots con la tabla ``bots``.

En backtest, :func:`run_offline` usa los mismos componentes con velas de DataFrames, una lista
como stream de señales y otra como stream de decisiones; las decisiones se pueden pasar al
``BacktestEngine`` de ``tm_engine`` con ``ScheduledDecisionSource``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pandas as pd
from tm_shared.contracts import DecisionEnvelope

from tm_agents.orchestrator.bot import BotCommand, BotRunner
from tm_agents.orchestrator.events import SignalEvent
from tm_agents.orchestrator.shared import SharedSignalLayer, bar_close_instants
from tm_agents.orchestrator.streams import MemorySink, SignalSource
from tm_agents.orchestrator.supervisor import BotSupervisor, CommandListener

log = logging.getLogger(__name__)


@dataclass
class OfflineResult:
    events: list[SignalEvent] = field(default_factory=list)
    decisions: list[DecisionEnvelope] = field(default_factory=list)
    shadow: list[DecisionEnvelope] = field(default_factory=list)


def run_offline(
    shared: SharedSignalLayer,
    runners: Sequence[BotRunner],
    start: datetime,
    end: datetime,
    *,
    commands: Iterable[tuple[datetime, BotCommand]] = (),
) -> OfflineResult:
    """Corre la capa compartida y los bots sobre ``(start, end]`` sin Redis.

    ``shared.sink`` y los sinks de los runners se respetan (normalmente ``MemorySink``); el
    resultado junta lo que se publicó en este recorrido. ``commands`` se aplican en el primer
    cierre de vela igual o posterior a su instante.
    """
    pending = sorted(commands, key=lambda c: c[0])
    result = OfflineResult()
    by_id = {r.bot_id: r for r in runners}
    for now in bar_close_instants(start, end, shared.timeframes()):
        while pending and pending[0][0] <= now:
            _, command = pending.pop(0)
            runner = by_id.get(command.bot_id)
            if runner is not None:
                result.decisions.extend(runner.handle_command(command, now))
        for event in shared.on_bar_close(now):
            result.events.append(event)
            for runner in runners:
                for envelope in runner.on_event(event):
                    (result.shadow if envelope.shadow else result.decisions).append(envelope)
    return result


class OrchestratorWorker:
    """Bucle del worker ``agents`` (ver el módulo). ``tick()`` es una iteración."""

    def __init__(
        self,
        shared: SharedSignalLayer | None,
        supervisor: BotSupervisor,
        signals: SignalSource,
        commands: CommandListener | None = None,
        *,
        sync_every: float = 60.0,
        settle_seconds: float = 5.0,
        max_catch_up: int = 60,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.shared = shared
        self.supervisor = supervisor
        self.signals = signals
        self.commands = commands
        self.sync_every = sync_every
        self.settle = pd.Timedelta(seconds=settle_seconds)
        self.max_catch_up = max_catch_up
        self._clock = clock
        self._last_bar: pd.Timestamp | None = None
        self._last_sync = float("-inf")

    def tick(self) -> dict[str, int]:
        now = pd.Timestamp(self._clock()).tz_convert("UTC")
        stats = {"events_published": 0, "events_consumed": 0, "commands": 0, "decisions": 0}
        if time.monotonic() - self._last_sync >= self.sync_every:
            self.supervisor.sync()
            self._last_sync = time.monotonic()
        # Se espera `settle` después del cierre para que Binance ya sirva la última vela.
        minute = (now - self.settle).floor("1min")
        if self.shared is not None:
            for close in self._pending_closes(minute):
                stats["events_published"] += len(self.shared.on_bar_close(close))
        if self.commands is not None:
            for command in self.commands.poll():
                stats["commands"] += 1
                stats["decisions"] += len(
                    self.supervisor.handle_command(command, now.to_pydatetime())
                )
        for event in self.signals.poll(now.to_pydatetime()):
            stats["events_consumed"] += 1
            stats["decisions"] += len(self.supervisor.on_event(event))
        return stats

    def _pending_closes(self, minute: pd.Timestamp) -> list[datetime]:
        """Cierres de minuto que no se han corrido (recupera hasta `max_catch_up`)."""
        if self._last_bar is not None and minute <= self._last_bar:
            return []
        first = minute if self._last_bar is None else self._last_bar + pd.Timedelta(minutes=1)
        first = max(first, minute - pd.Timedelta(minutes=self.max_catch_up - 1))
        self._last_bar = minute
        return [ts.to_pydatetime() for ts in pd.date_range(first, minute, freq="1min")]


def memory_sinks() -> tuple[MemorySink[Any], MemorySink[Any]]:
    """Par de sinks en memoria (decisiones y shadow) para backtest y pruebas."""
    return MemorySink(), MemorySink()


__all__ = ["OfflineResult", "OrchestratorWorker", "memory_sinks", "run_offline"]
