"""Cliente multimodal de Groq (vision) con salida JSON validada.

Reutiliza la lógica de reintentos y validación de `GroqClient` (texto-only): backoff
exponencial ante timeouts, 429, 5xx, respuestas sin JSON o fuera del esquema; otros 4xx
(llave inválida, modelo inexistente) fallan en el primer intento.

A diferencia del cliente texto-only, cada mensaje del usuario es una lista de partes
compatible con OpenAI/Groq: una parte `text` con el prompt y una parte `image_url` por cada
imagen, con los bytes codificados en base64 dentro de una URL `data:<media_type>;base64,...`.

Groq mantiene a los modelos vision actualizados en https://console.groq.com/docs/models; el
default (`DEFAULT_GROQ_VISION_MODEL`) usa Llama 4 Scout por ser el de vision más barato y
ampliamente disponible. El modelo real se toma de `GROQ_VISION_MODEL` cuando está definido.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from collections.abc import Callable, Sequence
from typing import Any

import httpx
from pydantic import ValidationError

from tm_agents.llm.base import Image, LLMError, MultimodalLLMClient, T
from tm_agents.llm.groq import (
    GROQ_BASE_URL,
    _RETRYABLE_STATUS,
    _retry_after,
    _RetryableError,
)

logger = logging.getLogger(__name__)

# Modelo de vision por defecto. Puede sobreescribirse con la env `GROQ_VISION_MODEL`.
DEFAULT_GROQ_VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"


class GroqMultimodalClient(MultimodalLLMClient):
    """`MultimodalLLMClient` para Groq (API compatible con OpenAI).

    La llave se recibe como argumento (ver `build_multimodal_llm_client`).
    """

    provider = "groq"

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_GROQ_VISION_MODEL,
        base_url: str = GROQ_BASE_URL,
        timeout: float = 20.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        backoff_max: float = 8.0,
        temperature: float = 0.0,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise ValueError("GroqMultimodalClient necesita una API key (GROQ_API_KEY)")
        if max_retries < 0:
            raise ValueError("max_retries no puede ser negativo")
        self.model = model
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.temperature = temperature
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._sleep = sleep
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(timeout=timeout)
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self._timeout = timeout

    def _payload(
        self,
        prompt: str,
        images: Sequence[Image],
        schema: type[T],
        system: str | None,
    ) -> dict[str, Any]:
        """Arma el payload OpenAI-compatible con `text` + `image_url` por imagen."""
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for image in images:
            b64 = base64.b64encode(image.data).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{image.media_type};base64,{b64}"},
                }
            )
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": content})
        return {
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

    def complete_multimodal_json(
        self,
        prompt: str,
        images: Sequence[Image],
        schema: type[T],
        *,
        system: str | None = None,
    ) -> T:
        payload = self._payload(prompt, images, schema, system)
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
                    "Groq vision intento %d/%d falló (%s); reintento en %.2fs",
                    attempt + 1,
                    self.max_retries + 1,
                    exc,
                    delay,
                )
                self._sleep(delay)
        raise LLMError(
            f"Groq vision falló tras {self.max_retries + 1} intentos: {last}"
        ) from last

    def close(self) -> None:
        if self._owns_client:
            self._http.close()
