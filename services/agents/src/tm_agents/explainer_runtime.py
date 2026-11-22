"""El ExplainerAgent en vivo: un event loop en su propio hilo y un sink hacia la API.

El worker ``agents`` es síncrono (un bucle con ``time.sleep``), así que el
``asyncio.create_task`` del BotRunner no tenía dónde correr y las explicaciones nunca se
generaban en vivo. :class:`ExplainerLoop` levanta un event loop de asyncio en un hilo
*daemon*; el BotRunner le entrega la corrutina (``explain_executor``) y sigue con la
siguiente decisión sin esperar al LLM.

Cuando la explicación está lista, :class:`WorkerApiExplanationSink` la guarda con
``POST /internal/decisions`` como una decisión de ``kind = "explanation"``::

    {"bot_id": "<uuid>", "kind": "explanation",
     "payload": {"envelope_id": "<bot>:<estrategia>:<símbolo>:...", "text": "..."}}

La tabla ``trades`` no tiene columna de explicación; el ``envelope_id`` es el mismo ``id``
del ``DecisionEnvelope`` que viaja a ``tm:decisions`` (y que se audita con
``kind = "decision"``), así que el frontend puede unir decisión y explicación.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
from collections.abc import Coroutine
from typing import Any
from uuid import UUID

from tm_shared.api_client import WorkerApiClient, WorkerApiError

log = logging.getLogger(__name__)

EXPLANATION_KIND = "explanation"


class ExplainerLoop:
    """Event loop de asyncio en un hilo daemon para correr corrutinas sin bloquear.

    Args:
        max_pending: tope de explicaciones en vuelo; si se rebasa, la nueva se descarta (la
            explicación es informativa y nunca debe acumular memoria si el LLM se cuelga).
    """

    def __init__(self, *, name: str = "explainer-loop", max_pending: int = 100) -> None:
        self.name = name
        self.max_pending = max_pending
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._pending: set[concurrent.futures.Future[Any]] = set()
        self._lock = threading.Lock()
        self.dropped = 0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._pending)

    def start(self) -> ExplainerLoop:
        if self.running:
            return self
        loop = asyncio.new_event_loop()
        ready = threading.Event()

        def run() -> None:
            asyncio.set_event_loop(loop)
            loop.call_soon(ready.set)
            loop.run_forever()
            loop.close()

        self._loop = loop
        self._thread = threading.Thread(target=run, name=self.name, daemon=True)
        self._thread.start()
        ready.wait(timeout=5)
        log.info("ExplainerLoop arrancado (hilo %s)", self.name)
        return self

    def submit(self, coro: Coroutine[Any, Any, Any]) -> concurrent.futures.Future[Any] | None:
        """Programa ``coro`` en el hilo del loop. ``None`` si se descartó."""
        if not self.running or self._loop is None:
            coro.close()
            log.debug("ExplainerLoop detenido: se descarta una explicación")
            self.dropped += 1
            return None
        with self._lock:
            if len(self._pending) >= self.max_pending:
                coro.close()
                self.dropped += 1
                log.warning(
                    "ExplainerLoop con %d explicaciones en vuelo: se descarta una", self.max_pending
                )
                return None
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            self._pending.add(future)
        future.add_done_callback(self._done)
        return future

    __call__ = submit

    def _done(self, future: concurrent.futures.Future[Any]) -> None:
        with self._lock:
            self._pending.discard(future)
        if not future.cancelled() and future.exception() is not None:
            log.warning("explicación fallida: %s", future.exception())

    def drain(self, timeout: float = 10.0) -> bool:
        """Espera a que terminen las explicaciones en vuelo (pruebas y apagado)."""
        with self._lock:
            futures = list(self._pending)
        _done, not_done = concurrent.futures.wait(futures, timeout=timeout)
        return not not_done

    def stop(self, timeout: float = 5.0) -> None:
        if self._loop is None or self._thread is None:
            return
        self.drain(timeout)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout)
        self._loop = None
        self._thread = None


class WorkerApiExplanationSink:
    """Guarda cada explicación con ``POST /internal/decisions`` (``kind = "explanation"``).

    Se llama desde el hilo del :class:`ExplainerLoop`; nunca lanza.
    """

    def __init__(self, client: WorkerApiClient) -> None:
        self.client = client
        self.saved = 0
        self.failed = 0

    def __call__(self, bot_id: str | None, envelope_id: str, text: str) -> None:
        payload: dict[str, Any] = {"envelope_id": envelope_id, "text": text}
        decision: dict[str, Any] = {"kind": EXPLANATION_KIND, "payload": payload}
        if bot_id and _is_uuid(bot_id):
            decision["bot_id"] = bot_id
        elif bot_id:
            payload["bot_id"] = bot_id
        try:
            # Repetirla solo duplica una fila informativa: se permite reintentar.
            self.client.post("/internal/decisions", {"decisions": [decision]}, idempotent=True)
        except WorkerApiError as exc:
            self.failed += 1
            log.warning("no se pudo guardar la explicación de %s: %s", envelope_id, exc)
            return
        self.saved += 1

    def for_bot(self, bot_id: str) -> Any:
        """Sink de dos argumentos ``(envelope_id, texto)`` para el BotRunner de ``bot_id``."""
        return lambda envelope_id, text: self(bot_id, envelope_id, text)


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except (TypeError, ValueError):
        return False
    return True


__all__ = ["EXPLANATION_KIND", "ExplainerLoop", "WorkerApiExplanationSink"]
