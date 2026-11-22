"""Cliente para cualquier API compatible con OpenAI (``POST {base}/chat/completions``).

Sirve para Groq (ver :mod:`tm_agents.llm.groq`) y para cualquier otro proveedor con la misma
API (OpenRouter, Together, un Ollama o LM Studio local, etc.) con
``LLM_PROVIDER=openai-compatible``.

Usa ``response_format = json_schema`` en modo best-effort (``strict: false``) porque el modo
estricto de varios proveedores exige que todas las propiedades sean obligatorias y no acepta
todas las restricciones que genera Pydantic (``minimum``/``maximum``). Si el servidor no
soporta ``json_schema``, ``response_format="json_object"`` pide JSON libre y el esquema va en
el prompt de sistema. La validación real la hace Pydantic del lado del cliente; si falla, se
reintenta.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Literal

import httpx

from tm_agents.llm._http import RetryingHTTPClient, _RetryableError, messages_for
from tm_agents.llm.base import T

ResponseFormat = Literal["json_schema", "json_object"]


class OpenAICompatibleClient(RetryingHTTPClient):
    """``LLMClient`` para una API compatible con OpenAI."""

    provider = "openai-compatible"
    label = "OpenAI-compatible"

    def __init__(
        self,
        api_key: str,
        *,
        model: str,
        base_url: str,
        timeout: float = 20.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        backoff_max: float = 8.0,
        temperature: float = 0.0,
        reasoning_effort: str | None = None,
        response_format: ResponseFormat = "json_schema",
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        if not model:
            raise ValueError(f"{self.label} necesita un modelo")
        if not base_url:
            raise ValueError(f"{self.label} necesita una URL base")
        kw: dict[str, Any] = {} if sleep is None else {"sleep": sleep}
        super().__init__(
            timeout=timeout,
            max_retries=max_retries,
            backoff_base=backoff_base,
            backoff_max=backoff_max,
            http_client=http_client,
            **kw,
        )
        self.model = model
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort
        self.response_format = response_format
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._headers = {"Content-Type": "application/json"}
        if api_key:  # un servidor local (Ollama) puede no pedir llave
            self._headers["Authorization"] = f"Bearer {api_key}"

    def _payload(self, prompt: str, schema: type[T], system: str | None) -> dict[str, Any]:
        json_schema = schema.model_json_schema()
        if self.response_format == "json_object":
            hint = f"Responde solo con JSON que cumpla este esquema: {json.dumps(json_schema)}"
            system = f"{system}\n\n{hint}" if system else hint
            response_format: dict[str, Any] = {"type": "json_object"}
        else:
            response_format = {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "strict": False, "schema": json_schema},
            }
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages_for(prompt, system),
            "temperature": self.temperature,
            "response_format": response_format,
        }
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        return payload

    def _attempt(self, prompt: str, schema: type[T], system: str | None) -> T:
        resp = self._post(self._url, self._payload(prompt, schema, system), self._headers)
        try:
            content = resp.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise _RetryableError(f"respuesta sin JSON válido: {exc}") from exc
        return self._validate(content, schema)
