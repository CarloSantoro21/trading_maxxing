import json

import httpx
import pytest
import respx
from pydantic import BaseModel, Field
from tm_agents.llm import (
    DEFAULT_GROQ_MODEL,
    FakeLLM,
    GroqClient,
    LLMClient,
    LLMError,
    build_llm_client,
)

URL = "https://api.groq.com/openai/v1/chat/completions"


class Answer(BaseModel):
    label: str
    score: float = Field(ge=-1, le=1)


def completion(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def client(**kw):
    delays = []
    c = GroqClient("gsk-test", sleep=delays.append, **kw)
    return c, delays


@respx.mock
def test_groq_ok_and_payload():
    route = respx.post(URL).mock(return_value=completion('{"label": "x", "score": 0.5}'))
    c, delays = client()
    out = c.complete_json("hola", Answer, system="sys")
    assert out == Answer(label="x", score=0.5)
    assert delays == []
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer gsk-test"
    body = json.loads(req.content)
    assert body["model"] == DEFAULT_GROQ_MODEL == "openai/gpt-oss-20b"
    assert body["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hola"},
    ]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["name"] == "Answer"
    assert body["response_format"]["json_schema"]["strict"] is False
    assert body["reasoning_effort"] == "low"
    c.close()


@respx.mock
def test_groq_without_system_or_reasoning():
    route = respx.post(URL).mock(return_value=completion('{"label": "x", "score": 0}'))
    c, _ = client(reasoning_effort=None)
    c.complete_json("hola", Answer)
    body = json.loads(route.calls.last.request.content)
    assert [m["role"] for m in body["messages"]] == ["user"]
    assert "reasoning_effort" not in body


@respx.mock
def test_groq_retries_then_succeeds():
    respx.post(URL).mock(
        side_effect=[
            httpx.Response(503, text="busy"),
            httpx.ConnectTimeout("lento"),
            httpx.ConnectError("caido"),
            completion("no es json"),
            completion('{"label": "x", "score": 7}'),  # fuera del esquema
            httpx.Response(400, json={"error": {"code": "json_validate_failed"}}),
            completion('{"label": "ok", "score": -1}'),
        ]
    )
    c, delays = client(max_retries=6, backoff_base=0.1, backoff_max=1.0)
    assert c.complete_json("p", Answer).label == "ok"
    assert delays == [0.1, 0.2, 0.4, 0.8, 1.0, 1.0]


@respx.mock
def test_groq_honors_retry_after():
    respx.post(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "2"}),
            httpx.Response(429, headers={"retry-after": "abc"}),
            completion('{"label": "ok", "score": 0}'),
        ]
    )
    c, delays = client(backoff_base=0.5)
    c.complete_json("p", Answer)
    assert delays == [2.0, 1.0]


@respx.mock
def test_groq_gives_up():
    respx.post(URL).mock(return_value=httpx.Response(500, text="boom"))
    c, delays = client(max_retries=2)
    with pytest.raises(LLMError, match="3 intentos"):
        c.complete_json("p", Answer)
    assert len(delays) == 2


@respx.mock
def test_groq_non_retryable_4xx():
    route = respx.post(URL).mock(return_value=httpx.Response(401, text="bad key"))
    c, delays = client()
    with pytest.raises(LLMError, match="401"):
        c.complete_json("p", Answer)
    assert route.call_count == 1 and delays == []


def test_groq_validation():
    with pytest.raises(ValueError):
        GroqClient("")
    with pytest.raises(ValueError):
        GroqClient("k", max_retries=-1)


def test_groq_custom_base_url_and_external_client():
    http = httpx.Client()
    with respx.mock:
        respx.post("http://local/v1/chat/completions").mock(
            return_value=completion('{"label": "x", "score": 0}')
        )
        c = GroqClient("k", base_url="http://local/v1/", http_client=http)
        c.complete_json("p", Answer)
        c.close()  # no cierra el cliente ajeno
    assert not http.is_closed
    http.close()


def test_fake_llm():
    fake = FakeLLM(
        [{"label": "a", "score": 0}, Answer(label="b", score=1), RuntimeError("caido"), {"x": 1}]
    )
    assert fake.complete_json("1", Answer).label == "a"
    assert fake.complete_json("2", Answer).label == "b"
    with pytest.raises(LLMError, match="caido"):
        fake.complete_json("3", Answer)
    with pytest.raises(LLMError, match="inválida"):
        fake.complete_json("4", Answer)
    with pytest.raises(LLMError, match="sin respuestas"):
        fake.complete_json("5", Answer)
    assert [p for p, _ in fake.calls] == ["1", "2", "3", "4", "5"]
    fake.close()


def test_fake_llm_responder_and_other_model():
    class Other(BaseModel):
        label: str
        score: float

    fake = FakeLLM(responder=lambda prompt, schema: Other(label=prompt, score=0))
    assert fake.complete_json("hi", Answer) == Answer(label="hi", score=0)


def test_build_llm_client():
    c = build_llm_client(
        {"GROQ_API_KEY": "k", "GROQ_MODEL": "openai/gpt-oss-120b", "LLM_MAX_RETRIES": "1"}
    )
    assert isinstance(c, GroqClient) and isinstance(c, LLMClient)
    assert c.model == "openai/gpt-oss-120b" and c.max_retries == 1
    c.close()
    assert isinstance(build_llm_client({"LLM_PROVIDER": "FAKE"}), FakeLLM)
    with pytest.raises(LLMError, match="GROQ_API_KEY"):
        build_llm_client({"LLM_PROVIDER": "groq"})
    with pytest.raises(ValueError, match="desconocido"):
        build_llm_client({"LLM_PROVIDER": "jev"})


def test_build_llm_client_reads_os_environ(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    assert isinstance(build_llm_client(), FakeLLM)
