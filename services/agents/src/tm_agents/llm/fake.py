"""LLM simulado para pruebas: no hace llamadas de red."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from pydantic import BaseModel, ValidationError

from tm_agents.llm.base import LLMClient, LLMError, T

Response = dict[str, Any] | BaseModel | Exception
Responder = Callable[[str, type[BaseModel]], Response]


class FakeLLM(LLMClient):
    """Devuelve respuestas predefinidas, en orden, o las que calcule `responder`.

    Cada respuesta puede ser un `dict` (se valida contra el esquema), una instancia de
    Pydantic o una excepción (se lanza como `LLMError`, igual que un proveedor caído).
    Guarda los prompts recibidos en `calls` para que las pruebas los revisen.
    """

    provider = "fake"

    def __init__(
        self, responses: Iterable[Response] | None = None, responder: Responder | None = None
    ) -> None:
        self._responses = list(responses or [])
        self._responder = responder
        self.calls: list[tuple[str, type[BaseModel]]] = []

    def complete_json(self, prompt: str, schema: type[T], *, system: str | None = None) -> T:
        self.calls.append((prompt, schema))
        if self._responder is not None:
            response = self._responder(prompt, schema)
        elif self._responses:
            response = self._responses.pop(0)
        else:
            raise LLMError("FakeLLM sin respuestas pendientes")
        if isinstance(response, Exception):
            raise LLMError(str(response)) from response
        if isinstance(response, schema):
            return response
        data = response.model_dump() if isinstance(response, BaseModel) else response
        try:
            return schema.model_validate(data)
        except ValidationError as exc:
            raise LLMError(f"respuesta simulada inválida: {exc}") from exc
