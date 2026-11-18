"""Pruebas del `GroqMultimodalClient` con `httpx.MockTransport`."""

from __future__ import annotations

import base64
import json

import httpx
import pytest
import respx
from pydantic import BaseModel, Field
from tm_agents.llm.base import Image, LLMError
from tm_agents.llm.groq_multimodal import DEFAULT_GROQ_VISION_MODEL, GroqMultimodalClient

URL = "https://api.groq.com/openai/v1/chat/completions"


class Verdict(BaseModel):
    label: str
    confidence: float = Field(ge=0, le=1)


def _completion(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _client(**kw):
    delays: list[float] = []
    client = GroqMultimodalClient("gsk-test", sleep=delays.append, **kw)
    return client, delays


@respx.mock
def test_groq_multimodal_happy_path_encodes_image() -> None:
    """La imagen viaja como `data:image/png;base64,...` y la respuesta se valida."""
    route = respx.post(URL).mock(return_value=_completion('{"label": "bull", "confidence": 0.9}'))
    client, delays = _client()

    img_bytes = b"\x89PNG\r\n\x1a\nFAKE"
    out = client.complete_multimodal_json(
        "describe la gráfica",
        [Image(data=img_bytes, media_type="image/png")],
        Verdict,
        system="eres un auditor",
    )

    assert out == Verdict(label="bull", confidence=0.9)
    assert delays == []  # un solo intento, sin reintentos

    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer gsk-test"
    body = json.loads(req.content)
    assert body["model"] == DEFAULT_GROQ_VISION_MODEL

    assert body["messages"][0] == {"role": "system", "content": "eres un auditor"}
    user_message = body["messages"][1]
    assert user_message["role"] == "user"
    parts = user_message["content"]
    assert parts[0] == {"type": "text", "text": "describe la gráfica"}
    assert parts[1]["type"] == "image_url"
    expected_b64 = base64.b64encode(img_bytes).decode("ascii")
    assert parts[1]["image_url"]["url"] == f"data:image/png;base64,{expected_b64}"

    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["name"] == "Verdict"

    client.close()


@respx.mock
def test_groq_multimodal_retries_on_429() -> None:
    """Un 429 se reintenta con el delay exponencial."""
    respx.post(URL).mock(
        side_effect=[
            httpx.Response(429, text="too many"),
            _completion('{"label": "bear", "confidence": 0.3}'),
        ]
    )
    client, delays = _client(backoff_base=0.5, backoff_max=1.0)

    out = client.complete_multimodal_json("prompt", [Image(data=b"x")], Verdict)
    assert out.label == "bear"
    assert delays == [0.5]


@respx.mock
def test_groq_multimodal_fails_fast_on_401() -> None:
    """Un 401 (llave inválida) falla al primer intento, sin reintentar."""
    route = respx.post(URL).mock(return_value=httpx.Response(401, text="bad key"))
    client, delays = _client()

    with pytest.raises(LLMError, match="401"):
        client.complete_multimodal_json("prompt", [Image(data=b"x")], Verdict)

    assert route.call_count == 1
    assert delays == []
