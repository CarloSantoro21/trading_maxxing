"""Cliente de Groq (API compatible con OpenAI) con salida JSON validada.

Es un :class:`~tm_agents.llm.openai_compat.OpenAICompatibleClient` con la URL de Groq, su
modelo por defecto y ``reasoning_effort=low``. Groq tiene un tier gratuito con límites por
minuto y por día. Además de los reintentos comunes, el 400 ``json_validate_failed`` de Groq
(el modelo no cumplió el esquema) también se reintenta.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx

from tm_agents.llm.openai_compat import OpenAICompatibleClient

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
# Modelo de producción de Groq (https://console.groq.com/docs/models), soporta json_schema
DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"


class GroqClient(OpenAICompatibleClient):
    """`LLMClient` para Groq. La llave se recibe como argumento (ver `build_llm_client`)."""

    provider = "groq"
    label = "Groq"

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
        super().__init__(
            api_key,
            model=model,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            backoff_base=backoff_base,
            backoff_max=backoff_max,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            http_client=http_client,
            sleep=sleep,
        )
