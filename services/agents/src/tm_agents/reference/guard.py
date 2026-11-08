"""TradingSleeveGuard: solo los sleeves con capital pueden abrir posiciones reales.

Una estrategia en ``shadow`` (o en cualquier estado sin capital) nunca abre posición en el
exchange: solo vive en su ledger de referencia. El motor (``AgenticStrategy``) consulta este
guardia antes de convertir una **entrada** en órdenes y la rechaza si el sleeve no está en
``active`` ni en ``probation``.

Los **cierres** (órdenes ``reduce_only``: en spot, ventas que no aumentan la posición) se
permiten siempre, en cualquier estado (#89). Si el LifecycleMonitor retira una estrategia con
posición abierta, el BotRunner emite la venta de cierre y esa venta tiene que llegar al
exchange; bloquearla dejaría la posición huérfana, protegida solo por su SL/TP de catástrofe.

Falla cerrado para las entradas: si la estrategia no existe o no se puede leer el registro,
no se abren posiciones (los cierres siguen pasando). El estado se guarda en caché ``ttl``
segundos para no pegarle a Supabase en cada decisión.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from tm_agents.registry import RegistryError, StrategyRegistry
from tm_agents.strategy_stats import StrategyState

log = logging.getLogger(__name__)


def can_send_orders(state: StrategyState | None) -> bool:
    """True solo para los estados que operan con capital (``active`` y ``probation``)."""
    return state is not None and StrategyState(state).gets_capital


class TradingSleeveGuard:
    """Decide si un sleeve puede enviar órdenes, según el estado de su estrategia."""

    def __init__(
        self,
        registry: StrategyRegistry,
        *,
        ttl: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.registry = registry
        self.ttl = ttl
        self.clock = clock
        self._cache: dict[str, tuple[float, StrategyState | None]] = {}

    def state(self, sleeve: str) -> StrategyState | None:
        """Estado de la estrategia dueña del sleeve (``None`` si no existe o hay error)."""
        now = self.clock()
        hit = self._cache.get(sleeve)
        if hit is not None and now - hit[0] < self.ttl:
            return hit[1]
        try:
            record = self.registry.find(sleeve)
        except RegistryError as exc:
            log.warning("no se pudo leer el estado de '%s'; no se opera: %s", sleeve, exc)
            return None
        state = record.state if record is not None else None
        self._cache[sleeve] = (now, state)
        return state

    def allows(self, sleeve: str, *, reduce_only: bool = False) -> bool:
        """True si el sleeve puede mandar la orden; un cierre (``reduce_only``) siempre puede."""
        if reduce_only:
            return True
        return can_send_orders(self.state(sleeve))

    def reason(self, sleeve: str) -> str:
        state = self.state(sleeve)
        if state is None:
            return f"el sleeve '{sleeve}' no tiene estrategia registrada; no se opera"
        return f"la estrategia '{sleeve}' está en {state.value}: sin capital no envía órdenes"

    def __call__(self, sleeve: str, *, reduce_only: bool = False) -> bool:
        return self.allows(sleeve, reduce_only=reduce_only)
