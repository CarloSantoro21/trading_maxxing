"""Portafolio de referencia por estrategia y modo shadow (issue #57).

- :mod:`~tm_agents.reference.ledger`: ledger a tamaño completo (fills al cierre, 0.1 %).
- :mod:`~tm_agents.reference.store`: ``reference_trades`` y ``reference_equity``.
- :mod:`~tm_agents.reference.service`: ciclo con el LifecycleMonitor cableado.
- :mod:`~tm_agents.reference.guard`: solo los sleeves con capital envían órdenes.

Las señales llegan de ``tm:signals`` con el formato único de :mod:`tm_agents.signals`
(``SignalEvent``, ``publish_signal``, ``RedisSignalSource``), el mismo que usa el orquestador.
"""

from tm_agents.reference.guard import TradingSleeveGuard, can_send_orders
from tm_agents.reference.ledger import (
    REFERENCE_CAPITAL,
    REFERENCE_FEE,
    ReferenceFill,
    ReferenceLedger,
    ReferenceTrade,
)
from tm_agents.reference.service import (
    MIN_SHADOW_DAYS,
    REFERENCE_CHECKPOINT,
    TRACKED_STATES,
    DrawdownBand,
    ReferenceService,
    shadow_status,
)
from tm_agents.reference.store import (
    EquityPoint,
    InMemoryReferenceStore,
    PostgRESTReferenceStore,
    ReferenceStore,
    ReferenceStoreError,
)

__all__ = [
    "MIN_SHADOW_DAYS",
    "REFERENCE_CAPITAL",
    "REFERENCE_CHECKPOINT",
    "REFERENCE_FEE",
    "TRACKED_STATES",
    "DrawdownBand",
    "EquityPoint",
    "InMemoryReferenceStore",
    "PostgRESTReferenceStore",
    "ReferenceFill",
    "ReferenceLedger",
    "ReferenceService",
    "ReferenceStore",
    "ReferenceStoreError",
    "ReferenceTrade",
    "TradingSleeveGuard",
    "can_send_orders",
    "shadow_status",
]
