"""Interfaz común para proveedores de LLM.

Solo el NewsAgent y el ExplainerAgent usan un LLM. Ambos piden siempre una respuesta JSON
que se valida contra un modelo Pydantic: si el proveedor devuelve algo que no cumple el
esquema, el cliente reintenta y, al agotar los intentos, lanza `LLMError`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """El proveedor no respondió o su respuesta no cumplió el esquema tras los reintentos."""


class LLMClient(ABC):
    """Cliente de LLM que devuelve objetos Pydantic ya validados."""

    provider: str = "base"

    @abstractmethod
    def complete_json(self, prompt: str, schema: type[T], *, system: str | None = None) -> T:
        """Envía `prompt` y devuelve la respuesta validada como instancia de `schema`.

        Lanza `LLMError` si no consigue una respuesta válida.
        """

    def close(self) -> None:  # noqa: B027 - por defecto no hay recursos que liberar
        """Libera los recursos del cliente (conexiones HTTP)."""
