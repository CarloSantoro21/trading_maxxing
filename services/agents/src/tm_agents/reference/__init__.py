"""Portafolio de referencia por estrategia y modo shadow (issue #57).

- :mod:`~tm_agents.reference.signals`: sobre y fuentes del stream ``tm:signals``.
- :mod:`~tm_agents.reference.ledger`: ledger a tamaño completo (fills al cierre, 0.1 %).
- :mod:`~tm_agents.reference.store`: ``reference_trades`` y ``reference_equity``.
- :mod:`~tm_agents.reference.service`: ciclo con el LifecycleMonitor cableado.
- :mod:`~tm_agents.reference.guard`: solo los sleeves con capital envían órdenes.
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
    TRACKED_STATES,
    DrawdownBand,
    ReferenceService,
    shadow_status,
)
from tm_agents.reference.signals import (
    SIGNALS_STREAM,
    InMemorySignalSource,
    RedisStreamSignalSource,
    SignalEnvelope,
    SignalSource,
    publish_signal,
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
    "REFERENCE_FEE",
    "SIGNALS_STREAM",
    "TRACKED_STATES",
    "DrawdownBand",
    "EquityPoint",
    "InMemoryReferenceStore",
    "InMemorySignalSource",
    "PostgRESTReferenceStore",
    "RedisStreamSignalSource",
    "ReferenceFill",
    "ReferenceLedger",
    "ReferenceService",
    "ReferenceStore",
    "ReferenceStoreError",
    "ReferenceTrade",
    "SignalEnvelope",
    "SignalSource",
    "TradingSleeveGuard",
    "can_send_orders",
    "publish_signal",
    "shadow_status",
]
