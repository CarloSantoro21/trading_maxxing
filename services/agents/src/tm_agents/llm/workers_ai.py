"""Cliente de Cloudflare Workers AI por su API REST, con salida JSON validada.

``POST https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{modelo}`` con
``Authorization: Bearer {CF_AI_API_TOKEN}`` (un token de la cuenta con permiso *Workers AI*).
El plan gratuito incluye 10,000 *neurons* al día, de sobra para clasificar unos cuantos
titulares cada pocos minutos.

Se pide JSON con ``response_format = {"type": "json_schema", "json_schema": <esquema>}``
(modo JSON de Workers AI). En ese modo ``result.response`` puede llegar ya como objeto o como
texto JSON; se aceptan ambos. Igual que con Groq, la validación final es de Pydantic y se
reintenta si la respuesta no cumple el esquema.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from tm_agents.llm._http import RetryingHTTPClient, _RetryableError, messages_for
from tm_agents.llm.base import LLMError, T

CF_API_BASE_URL = "https://api.cloudflare.com/client/v4"
# Modelo chico y gratis que soporta el modo JSON de Workers AI
DEFAULT_WORKERS_AI_MODEL = "@cf/meta/llama-3.1-8b-instruct"


class WorkersAIClient(RetryingHTTPClient):
    """``LLMClient`` para Workers AI (ver el módulo)."""

    provider = "workers-ai"
    label = "Workers AI"

    def __init__(
        self,
        account_id: str,
        api_token: str,
        *,
        model: str = DEFAULT_WORKERS_AI_MODEL,
        base_url: str = CF_API_BASE_URL,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        backoff_max: float = 8.0,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not account_id:
            raise ValueError("WorkersAIClient necesita CF_ACCOUNT_ID")
        if not api_token:
            raise ValueError("WorkersAIClient necesita CF_AI_API_TOKEN")
        super().__init__(
            timeout=timeout,
            max_retries=max_retries,
            backoff_base=backoff_base,
            backoff_max=backoff_max,
            http_client=http_client,
            sleep=sleep,
        )
        self.model = model
        self.temperature = temperature
        # Varios modelos de Workers AI cortan en 256 tokens por defecto: un lote de 20
        # titulares no cabe, así que se pide más.
        self.max_tokens = max_tokens
        self._url = f"{base_url.rstrip('/')}/accounts/{account_id}/ai/run/{model.lstrip('/')}"
        self._headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }

    def _payload(self, prompt: str, schema: type[T], system: str | None) -> dict[str, Any]:
        return {
            "messages": messages_for(prompt, system),
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_schema", "json_schema": schema.model_json_schema()},
        }

    def _attempt(self, prompt: str, schema: type[T], system: str | None) -> T:
        resp = self._post(self._url, self._payload(prompt, schema, system), self._headers)
        try:
            body = resp.json()
        except ValueError as exc:
            raise _RetryableError(f"respuesta sin JSON válido: {exc}") from exc
        if not isinstance(body, dict):
            raise _RetryableError("respuesta inesperada de Workers AI")
        if body.get("success") is False:
            raise LLMError(f"Workers AI respondió con error: {str(body.get('errors'))[:300]}")
        result = body.get("result")
        if not isinstance(result, dict) or "response" not in result:
            raise _RetryableError("Workers AI no devolvió result.response")
        return self._validate(result["response"], schema)
