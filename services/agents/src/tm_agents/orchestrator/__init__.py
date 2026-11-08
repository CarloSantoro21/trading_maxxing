"""AgentOrchestrator (#19): capa compartida de señales y un BotRunner por bot.

- :mod:`~tm_agents.orchestrator.shared`: señales, vetos y SimulationGate una vez para todos;
  publica :class:`~tm_agents.signals.SignalEvent` en ``tm:signals`` (el formato y su
  lectura y escritura viven en :mod:`tm_agents.signals`).
- :mod:`~tm_agents.orchestrator.bot`: cada bot dimensiona esas señales con su capital y su
  nivel de riesgo y publica ``DecisionEnvelope`` en ``tm:decisions`` (``tm:shadow`` para las
  estrategias en shadow).
- :mod:`~tm_agents.orchestrator.supervisor`: bots en ``running`` de Supabase y comandos de
  ``tm:commands`` (``start``, ``stop``, ``kill``).
- :mod:`~tm_agents.orchestrator.state`: el libro de cada bot tras un reinicio, desde ``trades``.
- :mod:`~tm_agents.orchestrator.runtime`: el worker en vivo y :func:`run_offline` para backtest.

Documentación: ``docs/arquitectura/orquestador.md``.
"""

from tm_agents.orchestrator.bot import (
    BotCommand,
    BotConfig,
    BotProfile,
    BotRunner,
    BotStatus,
    CommandType,
)
from tm_agents.orchestrator.exposure import exposure_for_level
from tm_agents.orchestrator.runtime import OfflineResult, OrchestratorWorker, run_offline
from tm_agents.orchestrator.shared import FrameBarsProvider, SharedSignalLayer
from tm_agents.orchestrator.state import (
    BotState,
    InMemoryTradeStateRepository,
    PostgRESTTradeStateRepository,
)
from tm_agents.orchestrator.streams import (
    COMMANDS_STREAM,
    DECISIONS_STREAM,
    SHADOW_STREAM,
    MemorySink,
)
from tm_agents.orchestrator.supervisor import BotSupervisor, InMemoryBotRepository
from tm_agents.signals import SIGNALS_STREAM, ListSignalSource, SignalEvent, StrategySnapshot

__all__ = [
    "COMMANDS_STREAM",
    "DECISIONS_STREAM",
    "SHADOW_STREAM",
    "SIGNALS_STREAM",
    "BotCommand",
    "BotConfig",
    "BotProfile",
    "BotRunner",
    "BotState",
    "BotStatus",
    "BotSupervisor",
    "CommandType",
    "FrameBarsProvider",
    "InMemoryBotRepository",
    "InMemoryTradeStateRepository",
    "ListSignalSource",
    "MemorySink",
    "OfflineResult",
    "OrchestratorWorker",
    "PostgRESTTradeStateRepository",
    "SharedSignalLayer",
    "SignalEvent",
    "StrategySnapshot",
    "exposure_for_level",
    "run_offline",
]
