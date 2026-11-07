"""Cliente de Groq (API compatible con OpenAI) con salida JSON validada.

Usa `response_format = json_schema` en modo best-effort (`strict: false`), porque el modo
estricto de Groq exige que todas las propiedades sean obligatorias y no acepta todas las
restricciones que genera Pydantic (por ejemplo `minimum`/`maximum`). La validación real la
hace Pydantic del lado del cliente; si falla, se reintenta.

Reintenta con backoff exponencial ante timeouts, errores de red, 429, 5xx, respuestas que no
son JSON o que no cumplen el esquema (incluido el 400 `json_validate_failed` de Groq). Otros
4xx (llave inválida, modelo inexistente) fallan de inmediato.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import ValidationError

from tm_agents.llm.base import LLMClient, LLMError, T

logger = logging.getLogger(__name__)

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
# Modelo de producción de Groq (https://console.groq.com/docs/models), soporta json_schema
DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"

_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


class _RetryableError(Exception):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class GroqClient(LLMClient):
    """`LLMClient` para Groq. La llave se recibe como argumento (ver `build_llm_client`)."""

    provider = "groq"

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_GROQ_MODEL,
        base_url: str = GROQ_BASE_URL,
        timeout: float = 20.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        backoff_max: float = 8.0,
        temperature: float = 0.0,
        reasoning_effort: str | None = "low",
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise ValueError("GroqClient necesita una API key (GROQ_API_KEY)")
        if max_retries < 0:
            raise ValueError("max_retries no puede ser negativo")
        self.model = model
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._sleep = sleep
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(timeout=timeout)
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self._timeout = timeout

    def _payload(self, prompt: str, schema: type[T], system: str | None) -> dict[str, Any]:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": False,
                    "schema": schema.model_json_schema(),
                },
            },
        }
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        return payload

    def _attempt(self, payload: dict[str, Any], schema: type[T]) -> T:
        try:
            resp = self._http.post(
                self._url, json=payload, headers=self._headers, timeout=self._timeout
            )
        except httpx.TimeoutException as exc:
            raise _RetryableError(f"timeout: {exc}") from exc
        except httpx.TransportError as exc:
            raise _RetryableError(f"error de red: {exc}") from exc

        if resp.status_code != 200:
            detail = resp.text[:300]
            if resp.status_code in _RETRYABLE_STATUS:
                raise _RetryableError(
                    f"HTTP {resp.status_code}: {detail}", _retry_after(resp.headers)
                )
            if resp.status_code == 400 and "json_validate_failed" in resp.text:
                raise _RetryableError(f"Groq no validó el JSON: {detail}")
            raise LLMError(f"Groq respondió HTTP {resp.status_code}: {detail}")

        try:
            content = resp.json()["choices"][0]["message"]["content"]
            data = json.loads(content)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise _RetryableError(f"respuesta sin JSON válido: {exc}") from exc
        try:
            return schema.model_validate(data)
        except ValidationError as exc:
            raise _RetryableError(f"JSON fuera del esquema {schema.__name__}: {exc}") from exc

    def _delay(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return min(retry_after, self.backoff_max)
        return min(self.backoff_base * 2**attempt, self.backoff_max)

    def complete_json(self, prompt: str, schema: type[T], *, system: str | None = None) -> T:
        payload = self._payload(prompt, schema, system)
        last: _RetryableError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return self._attempt(payload, schema)
            except _RetryableError as exc:
                last = exc
                if attempt == self.max_retries:
                    break
                delay = self._delay(attempt, exc.retry_after)
                logger.warning(
                    "Groq intento %d/%d falló (%s); reintento en %.2fs",
                    attempt + 1,
                    self.max_retries + 1,
                    exc,
                    delay,
                )
                self._sleep(delay)
        raise LLMError(f"Groq falló tras {self.max_retries + 1} intentos: {last}") from last

    def close(self) -> None:
        if self._owns_client:
            self._http.close()


def _retry_after(headers: httpx.Headers) -> float | None:
    value = headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        return None
