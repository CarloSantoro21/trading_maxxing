"""LLMClient: interfaz común de LLM y selección del proveedor por `LLM_PROVIDER`.

Proveedores disponibles:
- `groq` (por defecto): `GroqClient`, lee `GROQ_API_KEY`, `GROQ_MODEL`, `GROQ_BASE_URL`,
  `LLM_TIMEOUT_SECONDS` y `LLM_MAX_RETRIES`.
- `fake`: `FakeLLM` sin respuestas, útil para levantar el worker sin llaves.

Agregar otro proveedor (Claude, OpenAI, Jev) es implementar `LLMClient` y registrarlo en
`build_llm_client`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from tm_agents.llm.base import LLMClient, LLMError
from tm_agents.llm.fake import FakeLLM
from tm_agents.llm.groq import DEFAULT_GROQ_MODEL, GROQ_BASE_URL, GroqClient

SUPPORTED_PROVIDERS = ("groq", "fake")


def build_llm_client(env: Mapping[str, str] | None = None) -> LLMClient:
    """Construye el cliente indicado por `LLM_PROVIDER` (por defecto `groq`)."""
    env = os.environ if env is None else env
    provider = env.get("LLM_PROVIDER", "groq").strip().lower() or "groq"
    if provider == "groq":
        api_key = env.get("GROQ_API_KEY", "")
        if not api_key:
            raise LLMError("LLM_PROVIDER=groq requiere GROQ_API_KEY (ver .env.example)")
        return GroqClient(
            api_key,
            model=env.get("GROQ_MODEL") or DEFAULT_GROQ_MODEL,
            base_url=env.get("GROQ_BASE_URL") or GROQ_BASE_URL,
            timeout=float(env.get("LLM_TIMEOUT_SECONDS") or 20),
            max_retries=int(env.get("LLM_MAX_RETRIES") or 3),
        )
    if provider == "fake":
        return FakeLLM()
    raise ValueError(
        f"LLM_PROVIDER desconocido: '{provider}' (soportados: {', '.join(SUPPORTED_PROVIDERS)})"
    )


__all__ = [
    "DEFAULT_GROQ_MODEL",
    "SUPPORTED_PROVIDERS",
    "FakeLLM",
    "GroqClient",
    "LLMClient",
    "LLMError",
    "build_llm_client",
]
