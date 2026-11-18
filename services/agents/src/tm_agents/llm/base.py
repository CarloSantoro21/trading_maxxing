"""Interfaz común para proveedores de LLM.

Hoy el NewsAgent y el ExplainerAgent usan un `LLMClient` texto-only. El StrategyAuditorAgent
(ver issue #108) necesita además interpretar imágenes (gráficas) acompañadas de un prompt;
para eso se define `MultimodalLLMClient` como ABC paralela que acepta una lista de `Image`.

Ambas interfaces piden siempre una respuesta JSON que se valida contra un modelo Pydantic:
si el proveedor devuelve algo que no cumple el esquema, el cliente reintenta y, al agotar
los intentos, lanza `LLMError`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """El proveedor no respondió o su respuesta no cumplió el esquema tras los reintentos."""


@dataclass(frozen=True)
class Image:
    """Imagen que se adjunta al prompt del LLM multimodal.

    `data` son los bytes en crudo (PNG/JPEG/...). `media_type` es el MIME type usado para
    construir la URL `data:` cuando el proveedor expone una API compatible con OpenAI.
    """

    data: bytes
    media_type: str = "image/png"


class LLMClient(ABC):
    """Cliente de LLM texto-only que devuelve objetos Pydantic ya validados."""

    provider: str = "base"

    @abstractmethod
    def complete_json(self, prompt: str, schema: type[T], *, system: str | None = None) -> T:
        """Envía `prompt` y devuelve la respuesta validada como instancia de `schema`.

        Lanza `LLMError` si no consigue una respuesta válida.
        """

    def close(self) -> None:  # noqa: B027 - por defecto no hay recursos que liberar
        """Libera los recursos del cliente (conexiones HTTP)."""


class MultimodalLLMClient(ABC):
    """Cliente de LLM que acepta imágenes junto al prompt y devuelve JSON validado.

    Pensado para el StrategyAuditorAgent (#108): recibe la gráfica como `Image` y pide al
    modelo que razone sobre lo que ve. La validación Pydantic se hace del lado del cliente,
    como en `LLMClient`.
    """

    provider: str = "base"

    @abstractmethod
    def complete_multimodal_json(
        self,
        prompt: str,
        images: Sequence[Image],
        schema: type[T],
        *,
        system: str | None = None,
    ) -> T:
        """Envía `prompt` + `images` y devuelve la respuesta validada como `schema`.

        Lanza `LLMError` si no consigue una respuesta válida.
        """

    def close(self) -> None:  # noqa: B027 - por defecto no hay recursos que liberar
        """Libera los recursos del cliente (conexiones HTTP)."""
