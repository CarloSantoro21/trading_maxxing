"""LLM simulado para pruebas: no hace llamadas de red."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import Any

from pydantic import BaseModel, ValidationError

from tm_agents.llm.base import Image, LLMClient, LLMError, MultimodalLLMClient, T

Response = dict[str, Any] | BaseModel | Exception
Responder = Callable[[str, type[BaseModel]], Response]


def _coerce_response(response: Response, schema: type[T]) -> T:
    """Convierte la respuesta simulada en instancia de `schema` o lanza `LLMError`."""
    if isinstance(response, Exception):
        raise LLMError(str(response)) from response
    if isinstance(response, schema):
        return response
    data = response.model_dump() if isinstance(response, BaseModel) else response
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        raise LLMError(f"respuesta simulada inválida: {exc}") from exc


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
        return _coerce_response(response, schema)


class FakeMultimodalClient(MultimodalLLMClient):
    """Versión multimodal de `FakeLLM` para probar al StrategyAuditorAgent (#108).

    Además de guardar los prompts en `calls`, registra las imágenes recibidas en
    `received_images` (lista paralela a `calls`) para que las pruebas verifiquen que la
    gráfica llegó al cliente.
    """

    provider = "fake"

    def __init__(
        self, responses: Iterable[Response] | None = None, responder: Responder | None = None
    ) -> None:
        self._responses = list(responses or [])
        self._responder = responder
        self.calls: list[tuple[str, type[BaseModel]]] = []
        self.received_images: list[list[Image]] = []

    def complete_multimodal_json(
        self,
        prompt: str,
        images: Sequence[Image],
        schema: type[T],
        *,
        system: str | None = None,
    ) -> T:
        self.calls.append((prompt, schema))
        self.received_images.append(list(images))
        if self._responder is not None:
            response = self._responder(prompt, schema)
        elif self._responses:
            response = self._responses.pop(0)
        else:
            raise LLMError("FakeMultimodalClient sin respuestas pendientes")
        return _coerce_response(response, schema)
