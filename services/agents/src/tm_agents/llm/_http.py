"""Base común de los clientes HTTP de LLM: reintentos con backoff y manejo de errores.

Cada proveedor (Groq, OpenAI-compatible, Workers AI) solo implementa ``_attempt``, que hace
una petición y devuelve el objeto validado, o lanza ``_RetryableError`` (se reintenta) o
``LLMError`` (falla de inmediato). El ciclo de reintentos vive aquí:

- timeouts, errores de red, 408/409/429/5xx, respuestas sin JSON o fuera del esquema se
  reintentan con backoff exponencial (respetando ``Retry-After``);
- los demás 4xx (llave inválida, modelo inexistente) fallan de inmediato.
"""

from __future__ import annotations

import json
import logging
import time
from abc import abstractmethod
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import ValidationError

from tm_agents.llm.base import LLMClient, LLMError, T

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


class _RetryableError(Exception):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class RetryingHTTPClient(LLMClient):
    """``LLMClient`` sobre HTTP con reintentos. Las subclases implementan ``_attempt``."""

    #: Nombre legible del proveedor para logs y mensajes de error.
    label: str = "LLM"

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        backoff_max: float = 8.0,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries no puede ser negativo")
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self._sleep = sleep
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(timeout=timeout)
        self._timeout = timeout

    @abstractmethod
    def _attempt(self, prompt: str, schema: type[T], system: str | None) -> T:
        """Un intento: devuelve el objeto validado o lanza ``_RetryableError``/``LLMError``."""

    # --- utilidades para las subclases -----------------------------------------------------
    def _post(self, url: str, payload: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
        """POST que traduce red y códigos HTTP a ``_RetryableError``/``LLMError``."""
        try:
            resp = self._http.post(url, json=payload, headers=headers, timeout=self._timeout)
        except httpx.TimeoutException as exc:
            raise _RetryableError(f"timeout: {exc}") from exc
        except httpx.TransportError as exc:
            raise _RetryableError(f"error de red: {exc}") from exc
        if resp.status_code != 200:
            detail = resp.text[:300]
            if resp.status_code in RETRYABLE_STATUS:
                raise _RetryableError(
                    f"HTTP {resp.status_code}: {detail}", _retry_after(resp.headers)
                )
            if resp.status_code == 400 and "json_validate_failed" in resp.text:
                raise _RetryableError(f"{self.label} no validó el JSON: {detail}")
            raise LLMError(f"{self.label} respondió HTTP {resp.status_code}: {detail}")
        return resp

    @staticmethod
    def _validate(content: Any, schema: type[T]) -> T:
        """``content`` puede ser texto JSON o un dict ya parseado (Workers AI en modo JSON)."""
        try:
            data = json.loads(content) if isinstance(content, str | bytes) else content
        except ValueError as exc:
            raise _RetryableError(f"respuesta sin JSON válido: {exc}") from exc
        if data is None:
            raise _RetryableError("respuesta vacía")
        try:
            return schema.model_validate(data)
        except ValidationError as exc:
            raise _RetryableError(f"JSON fuera del esquema {schema.__name__}: {exc}") from exc

    def _delay(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return min(retry_after, self.backoff_max)
        return min(self.backoff_base * 2**attempt, self.backoff_max)

    # --- interfaz --------------------------------------------------------------------------
    def complete_json(self, prompt: str, schema: type[T], *, system: str | None = None) -> T:
        last: _RetryableError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return self._attempt(prompt, schema, system)
            except _RetryableError as exc:
                last = exc
                if attempt == self.max_retries:
                    break
                delay = self._delay(attempt, exc.retry_after)
                logger.warning(
                    "%s intento %d/%d falló (%s); reintento en %.2fs",
                    self.label,
                    attempt + 1,
                    self.max_retries + 1,
                    exc,
                    delay,
                )
                self._sleep(delay)
        raise LLMError(f"{self.label} falló tras {self.max_retries + 1} intentos: {last}") from last

    def close(self) -> None:
        if self._owns_client:
            self._http.close()


def messages_for(prompt: str, system: str | None) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return messages


def _retry_after(headers: httpx.Headers) -> float | None:
    value = headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        return None
