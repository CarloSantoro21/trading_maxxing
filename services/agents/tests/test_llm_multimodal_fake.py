"""Pruebas del `FakeMultimodalClient`: respuestas programadas, imágenes registradas
y errores de validación propagados como `LLMError`.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field
from tm_agents.llm.base import Image, LLMError, MultimodalLLMClient
from tm_agents.llm.fake import FakeMultimodalClient


class Verdict(BaseModel):
    label: str
    confidence: float = Field(ge=0, le=1)


def test_fake_multimodal_returns_scripted_json() -> None:
    """Las respuestas programadas se devuelven en orden y validadas como el esquema."""
    fake = FakeMultimodalClient(
        [
            {"label": "bull", "confidence": 0.8},
            Verdict(label="bear", confidence=0.2),
        ]
    )
    assert isinstance(fake, MultimodalLLMClient)

    out1 = fake.complete_multimodal_json("mira la gráfica", [Image(data=b"png-bytes")], Verdict)
    assert out1 == Verdict(label="bull", confidence=0.8)

    out2 = fake.complete_multimodal_json("y ahora esta", [], Verdict, system="eres un auditor")
    assert out2 == Verdict(label="bear", confidence=0.2)

    assert [prompt for prompt, _ in fake.calls] == ["mira la gráfica", "y ahora esta"]


def test_fake_multimodal_records_images() -> None:
    """Las imágenes recibidas quedan en `received_images`, paralelas a `calls`."""
    fake = FakeMultimodalClient([{"label": "flat", "confidence": 0.5}])

    img_a = Image(data=b"\x89PNG-A", media_type="image/png")
    img_b = Image(data=b"jpeg-B", media_type="image/jpeg")
    fake.complete_multimodal_json("prompt", [img_a, img_b], Verdict)

    assert fake.received_images == [[img_a, img_b]]
    assert fake.received_images[0][0].media_type == "image/png"
    assert fake.received_images[0][1].data == b"jpeg-B"


def test_fake_multimodal_validation_error_passes_through() -> None:
    """Una respuesta que no cumple el esquema se traduce en `LLMError`."""
    fake = FakeMultimodalClient([{"label": "x", "confidence": 2.0}])  # fuera del rango

    with pytest.raises(LLMError, match="inválida"):
        fake.complete_multimodal_json("prompt", [Image(data=b"x")], Verdict)
