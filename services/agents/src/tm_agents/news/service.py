"""NewsService: corre el NewsAgent en el worker solo cuando las noticias están prendidas.

En cada ``tick()`` (lo llama el ``OrchestratorWorker``) consulta ``enabled()`` (normalmente
``settings.get_bool("news_enabled")``, ver :mod:`tm_agents.orchestrator.settings`):

- **apagado** (default): no construye el NewsAgent, no llama al LLM ni a las fuentes y deja
  ``layer.news = None`` (la capa compartida no filtra por noticias);
- **prendido**: construye el NewsAgent la primera vez (con ``factory``), lo conecta como
  ``layer.news`` y cada ``interval`` segundos (``NEWS_REFRESH_SECONDS``, 300) corre
  ``refresh(symbols)``: titulares → clasificación → vetos.

Si el admin lo apaga en caliente, ``layer.news`` vuelve a ``None`` en el siguiente ciclo; al
prenderlo, se reutiliza el agente ya construido. Todo es fail-open: si construir el agente
falla, se registra y se reintenta en el siguiente intervalo.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from typing import Any

from tm_agents.news.agent import NewsAgent, RefreshResult

log = logging.getLogger(__name__)


class NewsService:
    """Ver el módulo. ``layer`` es cualquier objeto con atributo ``news`` (la capa compartida)."""

    def __init__(
        self,
        enabled: Callable[[], bool],
        factory: Callable[[], NewsAgent],
        symbols: Sequence[str],
        *,
        layer: Any | None = None,
        interval: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._enabled = enabled
        self._factory = factory
        self.symbols = tuple(symbols)
        self.layer = layer
        self.interval = interval
        self._clock = clock
        self.agent: NewsAgent | None = None
        self._last_refresh = float("-inf")
        self._last_build_attempt = float("-inf")
        self.last_result: RefreshResult | None = None

    def enabled(self) -> bool:
        try:
            return bool(self._enabled())
        except Exception as exc:  # un interruptor roto no detiene al worker: se apaga
            log.warning("no se pudo leer news_enabled; noticias apagadas (%s)", exc)
            return False

    def tick(self) -> RefreshResult | None:
        """Un ciclo; devuelve el resultado del ``refresh`` si tocó correrlo."""
        if not self.enabled():
            self._detach()
            return None
        now = self._clock()
        if self.agent is None:
            if now - self._last_build_attempt < self.interval:
                return None
            self._last_build_attempt = now
            try:
                self.agent = self._factory()
            except Exception as exc:
                log.warning("fail-open: no se pudo armar el NewsAgent (%s)", exc)
                return None
            log.info("noticias prendidas: NewsAgent listo")
        self._attach()
        if now - self._last_refresh < self.interval:
            return None
        self._last_refresh = now
        self.last_result = self.agent.refresh(self.symbols)
        return self.last_result

    def _attach(self) -> None:
        if self.layer is not None and getattr(self.layer, "news", None) is not self.agent:
            self.layer.news = self.agent

    def _detach(self) -> None:
        if self.layer is not None and getattr(self.layer, "news", None) is not None:
            log.info("noticias apagadas: la capa compartida deja de filtrar")
            self.layer.news = None


__all__ = ["NewsService"]
