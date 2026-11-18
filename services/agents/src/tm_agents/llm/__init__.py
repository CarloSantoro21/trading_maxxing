"""LLMClient: interfaz común de LLM y selección del proveedor por `LLM_PROVIDER`.

Proveedores disponibles:
- `groq` (por defecto): `GroqClient`, lee `GROQ_API_KEY`, `GROQ_MODEL`, `GROQ_BASE_URL`,
  `LLM_TIMEOUT_SECONDS` y `LLM_MAX_RETRIES`.
- `fake`: `FakeLLM` sin respuestas, útil para levantar el worker sin llaves.

Para el cliente multimodal (StrategyAuditorAgent, #108) se expone `build_multimodal_llm_client`:
usa Groq vision cuando `GROQ_API_KEY` esté definida y, si no, degrada a `FakeMultimodalClient`
para que los workers arranquen sin llaves (igual que el resto del stack en modo laboratorio).

Agregar otro proveedor (Claude, OpenAI, Jev) es implementar `LLMClient` y registrarlo en
`build_llm_client`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from tm_agents.llm.base import Image, LLMClient, LLMError, MultimodalLLMClient
from tm_agents.llm.fake import FakeLLM, FakeMultimodalClient
from tm_agents.llm.groq import DEFAULT_GROQ_MODEL, GROQ_BASE_URL, GroqClient
from tm_agents.llm.groq_multimodal import DEFAULT_GROQ_VISION_MODEL, GroqMultimodalClient

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


def build_multimodal_llm_client(env: Mapping[str, str] | None = None) -> MultimodalLLMClient:
    """Construye el cliente multimodal (vision) para el StrategyAuditorAgent (#108).

    Si `GROQ_API_KEY` no está definida, devuelve un `FakeMultimodalClient` sin respuestas
    para que el worker arranque y los tests puedan sustituir el cliente sin pedir llaves.
    """
    env = os.environ if env is None else env
    api_key = env.get("GROQ_API_KEY", "")
    if not api_key:
        return FakeMultimodalClient()
    return GroqMultimodalClient(
        api_key,
        model=env.get("GROQ_VISION_MODEL") or DEFAULT_GROQ_VISION_MODEL,
        base_url=env.get("GROQ_BASE_URL") or GROQ_BASE_URL,
        timeout=float(env.get("LLM_TIMEOUT_SECONDS") or 20),
        max_retries=int(env.get("LLM_MAX_RETRIES") or 3),
    )


__all__ = [
    "DEFAULT_GROQ_MODEL",
    "DEFAULT_GROQ_VISION_MODEL",
    "SUPPORTED_PROVIDERS",
    "FakeLLM",
    "FakeMultimodalClient",
    "GroqClient",
    "GroqMultimodalClient",
    "Image",
    "LLMClient",
    "LLMError",
    "MultimodalLLMClient",
    "build_llm_client",
    "build_multimodal_llm_client",
]
