"""StrategyAuditorAgent (#108): auditoría de cada señal antes de abrir o mantener un trade.

Primer slice: modelo y persistencia de las auditorías.

- :mod:`~tm_agents.strategy_auditor.models`: Pydantic :class:`SignalAudit`.
- :mod:`~tm_agents.strategy_auditor.store`: :class:`SignalAuditStore` + backend en memoria y
  PostgREST sobre la tabla ``public.signal_audits``.
"""

from tm_agents.strategy_auditor.models import Decision, Mode, SignalAudit, Status
from tm_agents.strategy_auditor.store import (
    InMemorySignalAuditStore,
    PostgRESTSignalAuditStore,
    SignalAuditStore,
    SignalAuditStoreError,
)

__all__ = [
    "Decision",
    "InMemorySignalAuditStore",
    "Mode",
    "PostgRESTSignalAuditStore",
    "SignalAudit",
    "SignalAuditStore",
    "SignalAuditStoreError",
    "Status",
]
