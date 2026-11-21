"""#118: LLM y noticias opcionales, apagados por defecto y con proveedores gratis.

Cada combinación de la configuración se prueba sin red real (respx o fakes):

- apagado (default): no se arma el NewsAgent, no hay LLM y la capa recibe ``news=None``;
- RSS + Workers AI;
- CryptoPanic + Groq;
- RSS + palabras clave (sin LLM);
- ExplainerAgent apagado (plantilla) y prendido (LLM), también en caliente.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import fakeredis
import httpx
import pytest
import redis
import respx
from pydantic import BaseModel
from tm_agents import __main__ as worker
from tm_agents.explainer import ExplainerAgent, explainer_from_env
from tm_agents.llm import (
    DEFAULT_WORKERS_AI_MODEL,
    FakeLLM,
    GroqClient,
    LLMError,
    OpenAICompatibleClient,
    WorkersAIClient,
    build_llm_client,
    llm_provider,
)
from tm_agents.news import (
    CryptoPanicClient,
    Impact,
    KeywordClassifier,
    LLMHeadlineClassifier,
    NewsAgent,
    NewsAPIError,
    NewsArticle,
    RSSNewsSource,
    news_agent_from_env,
    news_source_from_env,
)
from tm_agents.news.keywords import classify_headline, detect_assets
from tm_agents.news.rss import parse_feed
from tm_agents.news.service import NewsService
from tm_agents.orchestrator.runtime import OrchestratorWorker
from tm_agents.orchestrator.settings import (
    EnvSettingsProvider,
    StaticSettingsProvider,
    WorkerApiSettingsProvider,
    parse_bool,
    settings_from_env,
)

CF_URL = "https://api.cloudflare.com/client/v4/accounts/acc-1/ai/run/@cf/meta/llama-3.1-8b-instruct"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
POSTS_URL = "https://cryptopanic.com/api/developer/v2/posts/"
FEED_A = "https://feeds.test/a.xml"
FEED_B = "https://feeds.test/b.xml"

AI_ENV_VARS = (
    "LLM_PROVIDER",
    "NEWS_ENABLED",
    "EXPLAINER_ENABLED",
    "NEWS_SOURCE",
    "SETTINGS_SOURCE",
    "CF_ACCOUNT_ID",
    "CF_AI_API_TOKEN",
    "OPENAI_COMPAT_BASE_URL",
    "OPENAI_COMPAT_API_KEY",
    "OPENAI_COMPAT_MODEL",
    "CRYPTOPANIC_TOKEN",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in AI_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _now() -> datetime:
    return datetime.now(UTC)


def rss(*items: tuple[str, timedelta], categories: tuple[str, ...] = ()) -> str:
    """Feed RSS 2.0 con ``(título, antigüedad)`` por item."""
    cats = "".join(f"<category>{c}</category>" for c in categories)
    body = "".join(
        f"<item><title>{title}</title><link>https://news.test/{i}</link>"
        f"<guid>g-{i}-{title}</guid><pubDate>{format_datetime(_now() - ago)}</pubDate>{cats}"
        "</item>"
        for i, (title, ago) in enumerate(items)
    )
    head = '<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
    return f"{head}{body}</channel></rss>"


def atom(title: str, ago: timedelta) -> str:
    when = (_now() - ago).isoformat().replace("+00:00", "Z")
    return (
        '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">'
        f'<entry><title>{title}</title><id>urn:1</id><link href="https://news.test/atom"/>'
        f'<updated>{when}</updated><category term="Ethereum"/></entry></feed>'
    )


def classification(article_id_of, impact="high", symbols=("BTC",)):
    """Responder de Workers AI/Groq: clasifica cada id del prompt con ``impact``."""

    def items(prompt: str) -> list[dict]:
        ids = [json.loads(line)["id"] for line in prompt.splitlines()[1:]]
        return [
            {"id": i, "impact": impact if article_id_of(i) else "low", "symbols": list(symbols)}
            | {"sentiment": -0.5}
            for i in ids
        ]

    return items


# --- 1. apagado (configuración por defecto) ----------------------------------------------
def test_defaults_cost_nothing():
    assert llm_provider({}) == "none"
    assert build_llm_client({}) is None
    settings = settings_from_env({})
    assert isinstance(settings, EnvSettingsProvider)
    assert settings.get_bool("news_enabled") is False
    assert settings.get_bool("explainer_enabled") is False
    assert settings.get_bool("otra_cosa", default=True) is True


def test_news_off_never_builds_agent_and_layer_gets_none():
    class Layer:
        news = object()  # un filtro viejo debe desconectarse

    def boom():
        raise AssertionError("con NEWS_ENABLED=false no se arma el NewsAgent")

    layer = Layer()
    svc = NewsService(lambda: False, boom, ["BTCUSDT"], layer=layer)
    assert svc.tick() is None
    assert layer.news is None and svc.agent is None


@respx.mock
def test_build_worker_default_has_no_news_and_template_explainer(monkeypatch):
    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(lambda _u: fakeredis.FakeRedis()))
    w = worker.build_worker({"REDIS_URL": "redis://x"})
    assert w.shared.news is None
    assert isinstance(w.news, NewsService)
    assert w.supervisor.explainer is not None and not w.supervisor.explainer.uses_llm()
    w.news.tick()  # apagado: ni red ni agente (respx falla ante cualquier request)
    assert w.shared.news is None and w.news.agent is None


def test_build_optional_ai_falls_back_without_keys(caplog):
    settings, llm, explainer = worker.build_optional_ai(
        {"LLM_PROVIDER": "groq", "EXPLAINER_ENABLED": "true"}
    )
    assert llm is None  # falta GROQ_API_KEY: se sigue sin LLM
    assert settings.get_bool("explainer_enabled") is True
    assert not explainer.uses_llm()
    assert "sin configurar" in caplog.text


# --- 2. RSS + Workers AI -----------------------------------------------------------------
@respx.mock
def test_rss_with_workers_ai():
    respx.get(FEED_A).mock(
        return_value=httpx.Response(
            200,
            text=rss(
                ("Bitcoin exchange suffers breach", timedelta(minutes=20)),
                ("Solana memecoin rallies", timedelta(minutes=5)),  # sin BTC/ETH: fuera
                ("Old BTC story", timedelta(days=3)),  # vieja: fuera
            ),
        )
    )
    respx.get(FEED_B).mock(
        return_value=httpx.Response(200, text=atom("ETH update", timedelta(hours=1)))
    )
    titles: dict[str, str] = {}

    def cf(request):
        body = json.loads(request.content)
        prompt = body["messages"][-1]["content"]
        for line in prompt.splitlines()[1:]:
            row = json.loads(line)
            titles[row["id"]] = row["title"]
        items = classification(lambda i: "breach" in titles[i])(prompt)
        # Workers AI en modo JSON puede devolver el objeto ya parseado
        return httpx.Response(200, json={"success": True, "result": {"response": {"items": items}}})

    route = respx.post(CF_URL).mock(side_effect=cf)
    env = {
        "NEWS_SOURCE": "rss",
        "NEWS_RSS_FEEDS": f"{FEED_A}, {FEED_B}",
        "LLM_PROVIDER": "workers-ai",
        "CF_ACCOUNT_ID": "acc-1",
        "CF_AI_API_TOKEN": "cf-token",
    }
    agent = news_agent_from_env(env)
    assert isinstance(agent.llm, WorkersAIClient)
    assert isinstance(agent.classifier, LLMHeadlineClassifier)
    result = agent.refresh(["BTCUSDT", "ETHUSDT"])
    assert result.errors == []
    assert result.articles == 2  # el de BTC y el Atom de ETH
    assert agent.active_veto("BTCUSDT") is not None
    assert agent.active_veto("ETHUSDT") is None
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer cf-token"
    payload = json.loads(req.content)
    assert payload["response_format"]["type"] == "json_schema"
    assert "items" in payload["response_format"]["json_schema"]["properties"]
    assert payload["messages"][0]["role"] == "system"


# --- 3. CryptoPanic + Groq ---------------------------------------------------------------
@respx.mock
def test_cryptopanic_with_groq():
    published = (_now() - timedelta(minutes=30)).isoformat()
    respx.get(POSTS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": 7,
                        "title": "USDT depegs after reserve scare",
                        "published_at": published,
                        "instruments": [{"code": "USDT"}],
                    }
                ]
            },
        )
    )

    def groq(request):
        prompt = json.loads(request.content)["messages"][-1]["content"]
        items = classification(lambda _i: True, impact="critical", symbols=("USDT",))(prompt)
        content = json.dumps({"items": items})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    respx.post(GROQ_URL).mock(side_effect=groq)
    env = {
        "NEWS_SOURCE": "cryptopanic",
        "CRYPTOPANIC_TOKEN": "cp",
        "LLM_PROVIDER": "groq",
        "GROQ_API_KEY": "gsk",
    }
    agent = news_agent_from_env(env)
    assert isinstance(agent.llm, GroqClient)
    assert isinstance(agent.source, CryptoPanicClient)
    result = agent.refresh(["BTCUSDT"])
    assert [v.scope for v in result.vetoes] == ["*"]
    assert agent.active_veto("ETHUSDT").impact is Impact.CRITICAL


# --- 4. RSS + palabras clave, sin LLM -----------------------------------------------------
@respx.mock
def test_rss_with_keywords_and_no_llm():
    respx.get(FEED_A).mock(
        return_value=httpx.Response(
            200,
            text=rss(
                ("SEC sues exchange over Ether staking", timedelta(minutes=10)),
                ("Tether loses peg as panic spreads", timedelta(minutes=10)),
                ("Bitcoin price climbs to new high", timedelta(minutes=10)),
            ),
        )
    )
    env = {"NEWS_ENABLED": "true", "NEWS_RSS_FEEDS": FEED_A}  # LLM_PROVIDER=none
    agent = news_agent_from_env(env)
    assert agent.llm is None and isinstance(agent.classifier, KeywordClassifier)
    result = agent.refresh(["BTCUSDT", "ETHUSDT"])
    impacts = {a.title: c.impact for a, c in result.classified}
    assert impacts == {
        "SEC sues exchange over Ether staking": Impact.HIGH,
        "Tether loses peg as panic spreads": Impact.CRITICAL,
        "Bitcoin price climbs to new high": Impact.LOW,
    }
    assert {v.scope for v in result.vetoes} == {"ETH", "*"}
    assert len(respx.calls) == 1  # solo el feed: ningún LLM


@pytest.mark.parametrize(
    ("title", "impact"),
    [
        ("Hackers drain $40M from Ethereum bridge", Impact.HIGH),
        ("Exchange delists BTC pairs", Impact.HIGH),
        ("China bans bitcoin mining again", Impact.HIGH),
        ("Lender files for bankruptcy, halts withdrawals", Impact.CRITICAL),
        ("USDC de-pegged briefly", Impact.CRITICAL),
        ("Bitcoin hackathon kicks off in second week", Impact.LOW),  # ni hack ni SEC
        ("Banks warm up to bitcoin", Impact.LOW),  # "ban" no es "banks"
    ],
)
def test_keyword_classifier(title, impact):
    article = NewsArticle(id="1", title=title, published_at=_now())
    assert classify_headline(article).impact is impact


def test_detect_assets():
    assert detect_assets("Bitcoin and ether fall; Tether, USDC steady") == [
        "BTC",
        "ETH",
        "USDC",
        "USDT",
    ]
    assert detect_assets("Solana news") == []


# --- 5. ExplainerAgent apagado y prendido -------------------------------------------------
RATIONALE = {"symbol": "BTCUSDT", "action": "compra", "confidence": 0.7}


def test_explainer_off_uses_template_without_calling_llm():
    llm = FakeLLM([{"text": "no debería usarse"}])
    agent = explainer_from_env({}, llm=llm)  # EXPLAINER_ENABLED no definido = false
    text = asyncio.run(agent.explain(RATIONALE))
    assert text.startswith("Compra de BTCUSDT") and llm.calls == []


def test_explainer_on_uses_llm():
    llm = FakeLLM([{"text": "Se compró BTC con 70 % de confianza."}])
    agent = explainer_from_env({"EXPLAINER_ENABLED": "true"}, llm=llm)
    assert asyncio.run(agent.explain(RATIONALE)) == "Se compró BTC con 70 % de confianza."
    assert len(llm.calls) == 1


def test_explainer_on_without_llm_is_template():
    agent = explainer_from_env({"EXPLAINER_ENABLED": "1"})
    assert not agent.uses_llm()
    assert asyncio.run(agent.explain(RATIONALE)).startswith("Compra")


def test_explainer_hot_toggle_and_broken_switch():
    settings = StaticSettingsProvider({"explainer_enabled": False})
    llm = FakeLLM(responder=lambda _p, _s: {"text": "LLM"})
    agent = ExplainerAgent(llm, enabled=lambda: settings.get_bool("explainer_enabled"))
    assert asyncio.run(agent.explain(RATIONALE)) != "LLM"
    settings.values["explainer_enabled"] = "true"
    assert asyncio.run(agent.explain(RATIONALE)) == "LLM"

    def broken():
        raise RuntimeError("api caída")

    assert not ExplainerAgent(llm, enabled=broken).uses_llm()


# --- proveedores de LLM -------------------------------------------------------------------
class Answer(BaseModel):
    label: str


def test_build_llm_client_providers():
    cf = build_llm_client(
        {"LLM_PROVIDER": "workers-ai", "CF_ACCOUNT_ID": "a", "CF_AI_API_TOKEN": "t"}
    )
    assert isinstance(cf, WorkersAIClient) and cf.model == DEFAULT_WORKERS_AI_MODEL
    cf.close()
    oc = build_llm_client(
        {
            "LLM_PROVIDER": "openai_compatible",
            "OPENAI_COMPAT_BASE_URL": "http://local/v1",
            "OPENAI_COMPAT_MODEL": "m",
            "OPENAI_COMPAT_RESPONSE_FORMAT": "json_object",
        }
    )
    assert isinstance(oc, OpenAICompatibleClient) and oc.response_format == "json_object"
    oc.close()
    assert build_llm_client({"LLM_PROVIDER": "off"}) is None
    with pytest.raises(LLMError, match="CF_ACCOUNT_ID"):
        build_llm_client({"LLM_PROVIDER": "workers-ai"})
    with pytest.raises(LLMError, match="OPENAI_COMPAT_BASE_URL"):
        build_llm_client({"LLM_PROVIDER": "openai-compatible"})
    with pytest.raises(ValueError, match="RESPONSE_FORMAT"):
        build_llm_client(
            {
                "LLM_PROVIDER": "openai-compatible",
                "OPENAI_COMPAT_BASE_URL": "http://x",
                "OPENAI_COMPAT_MODEL": "m",
                "OPENAI_COMPAT_RESPONSE_FORMAT": "xml",
            }
        )


@respx.mock
def test_workers_ai_string_response_retries_and_errors():
    respx.post(CF_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "1"}),
            httpx.Response(200, json={"success": True, "result": {}}),
            httpx.Response(200, json={"success": True, "result": {"response": "no json"}}),
            httpx.Response(200, text="no json"),
            httpx.Response(200, json=["raro"]),
            httpx.Response(200, json={"success": True, "result": {"response": '{"label": "x"}'}}),
        ]
    )
    delays: list[float] = []
    c = WorkersAIClient("acc-1", "t", max_retries=5, sleep=delays.append)
    assert c.complete_json("p", Answer).label == "x"
    assert len(delays) == 5 and delays[0] == 1.0
    c.close()


@respx.mock
def test_workers_ai_api_error_is_not_retried():
    route = respx.post(CF_URL).mock(
        return_value=httpx.Response(200, json={"success": False, "errors": [{"code": 5007}]})
    )
    c = WorkersAIClient("acc-1", "t", sleep=lambda _d: None)
    with pytest.raises(LLMError, match="5007"):
        c.complete_json("p", Answer)
    assert route.call_count == 1
    with pytest.raises(ValueError):
        WorkersAIClient("", "t")
    with pytest.raises(ValueError):
        WorkersAIClient("a", "")


@respx.mock
def test_openai_compatible_json_object_without_key():
    route = respx.post("http://local/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": '{"label": "ok"}'}}]}
        )
    )
    c = OpenAICompatibleClient(
        "", model="llama3", base_url="http://local/v1", response_format="json_object"
    )
    assert c.complete_json("p", Answer, system="sys").label == "ok"
    req = route.calls.last.request
    body = json.loads(req.content)
    assert "authorization" not in req.headers
    assert body["response_format"] == {"type": "json_object"}
    assert (
        body["messages"][0]["content"].startswith("sys")
        and "esquema" in (body["messages"][0]["content"])
    )
    with pytest.raises(ValueError):
        OpenAICompatibleClient("k", model="", base_url="http://x")
    with pytest.raises(ValueError):
        OpenAICompatibleClient("k", model="m", base_url="")


# --- RSS ----------------------------------------------------------------------------------
@respx.mock
def test_rss_one_feed_failing_is_tolerated_all_failing_raises():
    respx.get(FEED_A).mock(return_value=httpx.Response(503))
    respx.get(FEED_B).mock(
        return_value=httpx.Response(200, text=rss(("BTC up", timedelta(minutes=1))))
    )
    src = RSSNewsSource([FEED_A, FEED_B])
    assert [a.title for a in src.fetch_posts(["BTC"])] == ["BTC up"]
    respx.get(FEED_B).mock(return_value=httpx.Response(200, text="<no es xml"))
    with pytest.raises(NewsAPIError, match="todos los feeds"):
        src.fetch_posts(["BTC"])
    src.close()


@respx.mock
def test_rss_network_error_and_oversized():
    respx.get(FEED_A).mock(side_effect=httpx.ConnectError("caído"))
    with pytest.raises(NewsAPIError, match="no respondió"):
        RSSNewsSource([FEED_A]).fetch_posts()
    respx.get(FEED_A).mock(return_value=httpx.Response(200, content=b" " * 5_000_001))
    with pytest.raises(NewsAPIError, match="excede"):
        RSSNewsSource([FEED_A]).fetch_posts()


def test_parse_feed_variants_and_source_from_env():
    items = parse_feed(
        rss(("Bitcoin", timedelta(0))).replace("<pubDate>", "<pubDate>bad ").encode()
    )
    assert items == []  # fecha inválida: se ignora
    entry = parse_feed(atom("Merge news", timedelta(hours=1)), source="x")
    assert entry[0].currencies == ("ETH",) and entry[0].url == "https://news.test/atom"
    tagged = parse_feed(rss(("Markets", timedelta(0)), categories=("Bitcoin",)))
    assert tagged[0].currencies == ("BTC",)
    src = news_source_from_env({})
    assert isinstance(src, RSSNewsSource) and len(src.feeds) == 3
    with pytest.raises(ValueError, match="NEWS_SOURCE"):
        news_source_from_env({"NEWS_SOURCE": "twitter"})
    with pytest.raises(ValueError):
        RSSNewsSource([" "])


# --- interruptores en caliente ------------------------------------------------------------
def test_parse_bool():
    assert parse_bool("Sí") and parse_bool(1) and parse_bool(True)
    assert not parse_bool("off") and not parse_bool(0)
    assert parse_bool("quizá", default=True) is True
    assert parse_bool(None) is False


def test_env_settings_invalid_value_uses_default():
    s = EnvSettingsProvider({"NEWS_ENABLED": "talvez"})
    assert s.get_bool("news_enabled") is False


@respx.mock
@pytest.mark.parametrize(
    "body",
    [
        {"news_enabled": True},
        {"settings": {"news_enabled": "true"}},
        [{"key": "news_enabled", "value": "1"}],
    ],
)
def test_worker_api_settings_formats(body):
    route = respx.get("https://api.test/internal/settings").mock(
        return_value=httpx.Response(200, json=body)
    )
    s = WorkerApiSettingsProvider("https://api.test/", "svc", fallback=StaticSettingsProvider())
    assert s.get_bool("news_enabled") is True
    assert s.get_bool("explainer_enabled") is False  # no viene: valor del fallback
    assert route.calls.last.request.headers["authorization"] == "Bearer svc"
    assert route.call_count == 1  # en caché durante refresh_seconds
    s.close()


@respx.mock
def test_worker_api_settings_cache_and_fail_safe():
    now = [0.0]
    route = respx.get("https://api.test/internal/settings").mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(200, json={"news_enabled": True}),
            httpx.ConnectError("caído"),
            httpx.Response(200, json="raro"),
        ]
    )
    fallback = StaticSettingsProvider({"news_enabled": False})
    s = WorkerApiSettingsProvider(
        "https://api.test", "svc", fallback=fallback, refresh_seconds=60, clock=lambda: now[0]
    )
    assert s.get_bool("news_enabled") is False  # nunca respondió: entorno
    now[0] = 61
    assert s.get_bool("news_enabled") is True
    now[0] = 30  # dentro de la ventana: no vuelve a pedir
    assert s.get_bool("news_enabled") is True and route.call_count == 2
    now[0] = 200
    assert s.get_bool("news_enabled") is True  # cae la API: últimos valores conocidos
    now[0] = 300
    assert s.get_bool("news_enabled") is True  # formato raro: igual
    assert route.call_count == 4


def test_settings_from_env_worker_api():
    s = settings_from_env(
        {
            "SETTINGS_SOURCE": "worker-api",
            "WORKER_API_URL": "https://a",
            "WORKER_SERVICE_TOKEN": "t",
        }
    )
    assert isinstance(s, WorkerApiSettingsProvider) and s.url == "https://a/internal/settings"
    with pytest.raises(ValueError, match="WORKER_API_URL"):
        settings_from_env({"SETTINGS_SOURCE": "worker-api"})
    with pytest.raises(ValueError, match="WORKER_SERVICE_TOKEN"):
        settings_from_env({"SETTINGS_SOURCE": "api", "WORKER_API_URL": "https://a"})
    with pytest.raises(ValueError, match="SETTINGS_SOURCE"):
        settings_from_env({"SETTINGS_SOURCE": "supabase"})


class _Source:
    def __init__(self):
        self.calls = 0

    def fetch_posts(self, currencies=()):
        self.calls += 1
        return []


def test_news_service_hot_toggle_interval_and_build_failure(caplog):
    class Layer:
        news = None

    settings = StaticSettingsProvider({"news_enabled": True})
    now = [0.0]
    source = _Source()
    builds: list[int] = []

    def factory():
        builds.append(1)
        if len(builds) == 1:
            raise RuntimeError("sin red")
        return NewsAgent(None, source)

    layer = Layer()
    svc = NewsService(
        lambda: settings.get_bool("news_enabled"),
        factory,
        ["BTCUSDT"],
        layer=layer,
        interval=60,
        clock=lambda: now[0],
    )
    assert svc.tick() is None and "no se pudo armar" in caplog.text
    now[0] = 10
    assert svc.tick() is None and len(builds) == 1  # espera el intervalo para reintentar
    now[0] = 70
    assert svc.tick() is not None and layer.news is svc.agent and source.calls == 1
    now[0] = 80
    assert svc.tick() is None and source.calls == 1  # aún no toca refrescar
    settings.values["news_enabled"] = False
    svc.tick()
    assert layer.news is None
    settings.values["news_enabled"] = True
    now[0] = 140
    svc.tick()
    assert layer.news is svc.agent and len(builds) == 2 and source.calls == 2

    def broken():
        raise RuntimeError("x")

    assert NewsService(broken, factory, []).tick() is None


def test_orchestrator_worker_survives_news_failure(caplog):
    class Supervisor:
        def sync(self):
            pass

    class Signals:
        def poll(self, now=None):
            return []

    class BrokenNews:
        def tick(self):
            raise RuntimeError("feed roto")

    w = OrchestratorWorker(None, Supervisor(), Signals(), news=BrokenNews())
    assert w.tick()["events_published"] == 0
    assert "falló el ciclo de noticias" in caplog.text
