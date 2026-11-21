"""LLMClient: interfaz común de LLM y selección del proveedor por ``LLM_PROVIDER``.

El LLM es **opcional y está apagado por defecto** (``LLM_PROVIDER=none``): así el sistema
completo corre sin ninguna llave y sin costo. Proveedores disponibles:

- ``none`` (por defecto): no hay LLM; ``build_llm_client`` devuelve ``None``. El NewsAgent
  usa el clasificador por palabras clave y el ExplainerAgent la plantilla.
- ``workers-ai``: :class:`WorkersAIClient` (Cloudflare, 10,000 neurons al día gratis). Lee
  ``CF_ACCOUNT_ID``, ``CF_AI_API_TOKEN`` y ``WORKERS_AI_MODEL``.
- ``groq``: :class:`GroqClient` (tier gratuito). Lee ``GROQ_API_KEY``, ``GROQ_MODEL`` y
  ``GROQ_BASE_URL``.
- ``openai-compatible``: :class:`OpenAICompatibleClient` para cualquier API compatible con
  OpenAI. Lee ``OPENAI_COMPAT_BASE_URL``, ``OPENAI_COMPAT_API_KEY``, ``OPENAI_COMPAT_MODEL`` y
  ``OPENAI_COMPAT_RESPONSE_FORMAT`` (``json_schema`` o ``json_object``).
- ``fake``: :class:`FakeLLM` sin respuestas, solo para pruebas.

Todos los HTTP leen ``LLM_TIMEOUT_SECONDS`` y ``LLM_MAX_RETRIES``. Agregar otro proveedor es
implementar ``LLMClient`` y registrarlo en ``build_llm_client``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from tm_agents.llm.base import LLMClient, LLMError
from tm_agents.llm.fake import FakeLLM
from tm_agents.llm.groq import DEFAULT_GROQ_MODEL, GROQ_BASE_URL, GroqClient
from tm_agents.llm.openai_compat import OpenAICompatibleClient
from tm_agents.llm.workers_ai import DEFAULT_WORKERS_AI_MODEL, WorkersAIClient

SUPPORTED_PROVIDERS = ("none", "workers-ai", "groq", "openai-compatible", "fake")
DEFAULT_PROVIDER = "none"

_ALIASES = {
    "": "none",
    "off": "none",
    "workers_ai": "workers-ai",
    "workersai": "workers-ai",
    "cloudflare": "workers-ai",
    "openai_compatible": "openai-compatible",
    "openai": "openai-compatible",
}


def llm_provider(env: Mapping[str, str] | None = None) -> str:
    """Proveedor normalizado de ``LLM_PROVIDER`` (por defecto ``none``)."""
    env = os.environ if env is None else env
    raw = (env.get("LLM_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    return _ALIASES.get(raw, raw)


def build_llm_client(env: Mapping[str, str] | None = None) -> LLMClient | None:
    """Construye el cliente de ``LLM_PROVIDER``; ``None`` si es ``none`` (el default).

    Lanza ``LLMError`` si falta una llave del proveedor elegido y ``ValueError`` si el
    proveedor no existe.
    """
    env = os.environ if env is None else env
    provider = llm_provider(env)
    timeout = float(env.get("LLM_TIMEOUT_SECONDS") or 20)
    retries = int(env.get("LLM_MAX_RETRIES") or 3)
    if provider == "none":
        return None
    if provider == "workers-ai":
        account, token = env.get("CF_ACCOUNT_ID", ""), env.get("CF_AI_API_TOKEN", "")
        if not account or not token:
            raise LLMError(
                "LLM_PROVIDER=workers-ai requiere CF_ACCOUNT_ID y CF_AI_API_TOKEN "
                "(ver .env.example)"
            )
        return WorkersAIClient(
            account,
            token,
            model=env.get("WORKERS_AI_MODEL") or DEFAULT_WORKERS_AI_MODEL,
            timeout=float(env.get("LLM_TIMEOUT_SECONDS") or 30),
            max_retries=retries,
        )
    if provider == "groq":
        api_key = env.get("GROQ_API_KEY", "")
        if not api_key:
            raise LLMError("LLM_PROVIDER=groq requiere GROQ_API_KEY (ver .env.example)")
        return GroqClient(
            api_key,
            model=env.get("GROQ_MODEL") or DEFAULT_GROQ_MODEL,
            base_url=env.get("GROQ_BASE_URL") or GROQ_BASE_URL,
            timeout=timeout,
            max_retries=retries,
        )
    if provider == "openai-compatible":
        base_url, model = env.get("OPENAI_COMPAT_BASE_URL", ""), env.get("OPENAI_COMPAT_MODEL", "")
        if not base_url or not model:
            raise LLMError(
                "LLM_PROVIDER=openai-compatible requiere OPENAI_COMPAT_BASE_URL y "
                "OPENAI_COMPAT_MODEL (ver .env.example)"
            )
        fmt = (env.get("OPENAI_COMPAT_RESPONSE_FORMAT") or "json_schema").strip().lower()
        if fmt not in ("json_schema", "json_object"):
            raise ValueError(f"OPENAI_COMPAT_RESPONSE_FORMAT inválido: '{fmt}'")
        return OpenAICompatibleClient(
            env.get("OPENAI_COMPAT_API_KEY", ""),
            model=model,
            base_url=base_url,
            timeout=timeout,
            max_retries=retries,
            response_format=fmt,  # type: ignore[arg-type]
        )
    if provider == "fake":
        return FakeLLM()
    raise ValueError(
        f"LLM_PROVIDER desconocido: '{provider}' (soportados: {', '.join(SUPPORTED_PROVIDERS)})"
    )


__all__ = [
    "DEFAULT_GROQ_MODEL",
    "DEFAULT_PROVIDER",
    "DEFAULT_WORKERS_AI_MODEL",
    "SUPPORTED_PROVIDERS",
    "FakeLLM",
    "GroqClient",
    "LLMClient",
    "LLMError",
    "OpenAICompatibleClient",
    "WorkersAIClient",
    "build_llm_client",
    "llm_provider",
]
