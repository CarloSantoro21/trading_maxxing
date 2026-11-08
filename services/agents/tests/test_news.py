import logging
from datetime import UTC, datetime, timedelta

import fakeredis
import httpx
import pytest
import redis
import respx
from tm_agents.base import AgentContext
from tm_agents.llm import FakeLLM
from tm_agents.news import (
    GLOBAL_SCOPE,
    CryptoPanicClient,
    HeadlineClassification,
    Impact,
    InMemoryVetoStore,
    NewsAgent,
    NewsAPIError,
    NewsArticle,
    RedisVetoStore,
    Veto,
    VetoPolicy,
    news_agent_from_env,
    split_symbol,
)
from tm_agents.news.veto import veto_store_from_env

NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)
POSTS_URL = "https://cryptopanic.com/api/developer/v2/posts/"


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


def article(id_="1", title="t", ago=timedelta(minutes=10), currencies=("BTC",)):
    return NewsArticle(id=id_, title=title, published_at=NOW - ago, currencies=currencies)


class FakeSource:
    def __init__(self, articles=(), error=None):
        self.articles = list(articles)
        self.error = error
        self.calls = []

    def fetch_posts(self, currencies=()):
        self.calls.append(list(currencies))
        if self.error:
            raise self.error
        return list(self.articles)


def item(id_="1", impact="low", symbols=(), sentiment=0.0):
    return {"id": id_, "impact": impact, "symbols": list(symbols), "sentiment": sentiment}


def make_agent(articles=(), responses=(), store=None, clock=None, **kw):
    clock = clock or Clock()
    llm = FakeLLM([{"items": r} if isinstance(r, list) else r for r in responses])
    source = FakeSource(articles)
    agent = NewsAgent(llm, source, store or InMemoryVetoStore(clock), clock=clock, **kw)
    return agent, llm, source


# --- modelos ------------------------------------------------------------------------------
def test_classification_validation():
    c = HeadlineClassification(
        id="1", impact="high", symbols=[" eth", "BTC", "btc", ""], sentiment=-1
    )
    assert c.symbols == ["BTC", "ETH"] and c.impact is Impact.HIGH
    with pytest.raises(ValueError):
        HeadlineClassification(id="1", impact="high", sentiment=1.5)
    with pytest.raises(ValueError):
        HeadlineClassification(id="1", impact="medium", sentiment=0)


def test_article_naive_date_is_utc():
    a = NewsArticle(id="1", title="t", published_at=datetime(2026, 1, 1))
    assert a.published_at.tzinfo is UTC


@pytest.mark.parametrize(
    "symbol,expected",
    [("BTCUSDT", ("BTC", "USDT")), ("ethusdc", ("ETH", "USDC")), ("BTC", ("BTC", None))],
)
def test_split_symbol(symbol, expected):
    assert split_symbol(symbol) == expected


# --- política de veto ---------------------------------------------------------------------
def test_high_impact_vetoes_symbol_4h():
    agent, llm, source = make_agent(
        [article("1", "Exchange X hackeado", currencies=("ETH",))],
        [[item("1", "high", ["ETH"], -0.8)]],
    )
    result = agent.refresh(["BTCUSDT", "ETHUSDT"])
    assert not result.failed_open and result.articles == 1
    [veto] = result.vetoes
    assert veto.scope == "ETH" and veto.impact is Impact.HIGH
    assert veto.expires_at == NOW - timedelta(minutes=10) + timedelta(hours=4)
    assert source.calls == [["BTC", "ETH", "USDC", "USDT"]]
    assert agent.active_veto("ETHUSDT") == veto
    assert agent.active_veto("BTCUSDT") is None
    ctx = AgentContext(symbol="ETHUSDT", now=NOW, bars={})
    assert agent.veto(ctx) == veto
    prompt, _ = llm.calls[0]
    assert "Exchange X hackeado" in prompt and '"id": "1"' in prompt


def test_critical_vetoes_everything_24h():
    agent, _, _ = make_agent(
        [article("9", "USDT pierde la paridad", currencies=("USDT",))],
        [[item("9", "critical", ["USDT"], -1)]],
    )
    [veto] = agent.refresh(["BTCUSDT"]).vetoes
    assert veto.is_global and veto.scope == GLOBAL_SCOPE
    assert veto.ttl_seconds(NOW) == int(
        (timedelta(hours=24) - timedelta(minutes=10)).total_seconds()
    )
    assert agent.active_veto("BTCUSDT") == veto
    assert agent.active_veto("SOLUSDT") == veto


def test_low_impact_does_not_veto():
    agent, _, _ = make_agent([article()], [[item("1", "low", ["BTC"], 0.9)]])
    result = agent.refresh(["BTCUSDT"])
    assert result.vetoes == [] and len(result.classified) == 1
    assert agent.active_veto("BTCUSDT") is None


def test_high_without_symbols_uses_article_currencies_or_skips(caplog):
    agent, _, _ = make_agent(
        [article("1", currencies=("BTC",)), article("2", currencies=())],
        [[item("1", "high"), item("2", "high")]],
    )
    with caplog.at_level(logging.WARNING):
        vetoes = agent.refresh(["BTCUSDT"]).vetoes
    assert [v.scope for v in vetoes] == ["BTC"]
    assert "sin activos" in caplog.text


def test_quote_asset_veto_blocks_pair():
    agent, _, _ = make_agent([article(currencies=("USDT",))], [[item("1", "high", ["USDT"])]])
    agent.refresh(["BTCUSDT"])
    assert agent.active_veto("BTCUSDT").scope == "USDT"


def test_old_news_does_not_veto_and_veto_expires():
    clock = Clock()
    agent, _, _ = make_agent(
        [article("old", ago=timedelta(hours=5)), article("new", ago=timedelta(hours=3))],
        [[item("old", "high", ["BTC"]), item("new", "high", ["BTC"])]],
        clock=clock,
    )
    [veto] = agent.refresh(["BTCUSDT"]).vetoes
    assert veto.article_id == "new"
    clock.now = NOW + timedelta(hours=1, minutes=1)
    assert agent.active_veto("BTCUSDT") is None


def test_future_published_at_is_capped_to_now():
    agent, _, _ = make_agent([article(ago=-timedelta(hours=2))], [[item("1", "high", ["BTC"])]])
    [veto] = agent.refresh(["BTCUSDT"]).vetoes
    assert veto.expires_at == NOW + timedelta(hours=4)


def test_custom_policy():
    agent, _, _ = make_agent(
        [article()],
        [[item("1", "high", ["BTC"])]],
        policy=VetoPolicy(high_duration=timedelta(hours=1)),
    )
    [veto] = agent.refresh(["BTCUSDT"]).vetoes
    assert veto.expires_at == NOW - timedelta(minutes=10) + timedelta(hours=1)


def test_seen_articles_are_not_reclassified_and_unknown_ids_ignored(caplog):
    agent, llm, source = make_agent(
        [article("1")], [[item("1", "low"), item("zzz", "critical")]], max_seen=1
    )
    with caplog.at_level(logging.WARNING):
        result = agent.refresh(["BTCUSDT"])
    assert len(result.classified) == 1 and result.vetoes == []
    assert "id desconocido" in caplog.text
    assert agent.refresh(["BTCUSDT"]).articles == 0
    assert len(llm.calls) == 1
    source.articles = [article("2")]
    llm._responses.append({"items": [item("2", "low")]})
    agent.refresh(["BTCUSDT"])
    source.articles = [article("1")]  # max_seen=1: el 1 ya se olvidó
    assert agent.fetch_news(["BTCUSDT"]) == [article("1")]


def test_batches():
    arts = [article(str(i)) for i in range(5)]
    responses = [[item(str(i)) for i in range(j, min(j + 2, 5))] for j in range(0, 5, 2)]
    agent, llm, _ = make_agent(arts, responses, batch_size=2)
    assert len(agent.refresh(["BTCUSDT"]).classified) == 5
    assert len(llm.calls) == 3
    with pytest.raises(ValueError):
        make_agent(batch_size=0)


def test_repeated_veto_is_not_extended():
    store = InMemoryVetoStore(Clock())
    agent, _, _ = make_agent(store=store)
    a = article("1")
    c = HeadlineClassification(id="1", impact="high", symbols=["BTC"], sentiment=-1)
    assert len(agent.apply([(a, c)])) == 1
    assert agent.apply([(a, c)]) == []


# --- fail-open ----------------------------------------------------------------------------
def test_fail_open_when_llm_fails(caplog):
    agent, _, _ = make_agent([article()], [RuntimeError("groq caido")])
    with caplog.at_level(logging.WARNING):
        result = agent.refresh(["BTCUSDT"])
    assert result.failed_open and result.vetoes == []
    assert "fail-open" in caplog.text
    assert agent.active_veto("BTCUSDT") is None
    # el titular no se marcó como visto: se reintenta en el siguiente ciclo
    assert agent.fetch_news(["BTCUSDT"]) == [article()]


def test_fail_open_when_llm_returns_garbage():
    agent, _, _ = make_agent([article()], [{"items": [{"id": "1", "impact": "x"}]}])
    assert agent.refresh(["BTCUSDT"]).failed_open


def test_fail_open_when_news_api_fails(caplog):
    agent, llm, source = make_agent()
    source.error = NewsAPIError("503")
    with caplog.at_level(logging.WARNING):
        result = agent.refresh(["BTCUSDT"])
    assert result.failed_open and result.errors == ["news: 503"]
    assert llm.calls == []


def test_no_articles_skips_llm():
    agent, llm, _ = make_agent()
    result = agent.refresh(["BTCUSDT"])
    assert result.articles == 0 and not result.failed_open and llm.calls == []


# --- almacenes de vetos -------------------------------------------------------------------
def veto(scope="BTC", hours=4, impact=Impact.HIGH):
    return Veto(
        scope=scope,
        impact=impact,
        reason="r",
        article_id="a",
        expires_at=NOW + timedelta(hours=hours),
    )


def test_memory_store():
    clock = Clock()
    store = InMemoryVetoStore(clock)
    assert not store.put(veto(hours=-1))
    assert store.put(veto(hours=2))
    assert not store.put(veto(hours=1))
    assert store.put(veto(hours=3))
    assert store.put(veto("*", 24, Impact.CRITICAL))
    assert {v.scope for v in store.active()} == {"BTC", "*"}
    clock.now = NOW + timedelta(hours=5)
    assert store.get("BTC") is None and store.get("ETH") is None
    assert [v.scope for v in store.active()] == ["*"]


def test_redis_store_ttl_and_no_extension():
    r = fakeredis.FakeRedis()
    clock = Clock()
    store = RedisVetoStore(r, clock)
    assert store.put(veto(hours=4))
    assert 0 < r.ttl("tm:news:veto:BTC") <= 4 * 3600
    assert not store.put(veto(hours=1))
    assert not store.put(veto(hours=-1))
    assert store.get("BTC") == veto(hours=4)
    assert store.put(veto("*", 24, Impact.CRITICAL))
    assert {v.scope for v in store.active()} == {"BTC", "*"}
    # otro proceso con el mismo Redis ve los vetos
    assert RedisVetoStore(r, clock).get("*").impact is Impact.CRITICAL
    clock.now = NOW + timedelta(hours=5)  # fakeredis no avanza con el reloj simulado
    assert store.get("BTC") is None


def test_redis_store_ignores_corrupt(caplog):
    r = fakeredis.FakeRedis()
    r.set("tm:news:veto:BTC", b"{no json")
    store = RedisVetoStore(r, Clock())
    with caplog.at_level(logging.WARNING):
        assert store.get("BTC") is None
    assert "corrupto" in caplog.text


class BrokenRedis:
    def get(self, *a, **k):
        raise redis.ConnectionError("down")

    set = get
    scan_iter = get


def test_redis_store_falls_back_to_memory(caplog):
    store = RedisVetoStore(BrokenRedis(), Clock())
    with caplog.at_level(logging.WARNING):
        assert store.put(veto())
        assert store.get("BTC") == veto()
        assert store.active() == [veto()]
    assert "Redis no disponible" in caplog.text


def test_veto_store_from_env():
    assert isinstance(veto_store_from_env({}), InMemoryVetoStore)
    assert isinstance(
        veto_store_from_env({"REDIS_URL": "redis://localhost:6379/0"}), RedisVetoStore
    )


def test_agent_with_redis_end_to_end():
    store = RedisVetoStore(fakeredis.FakeRedis(), Clock())
    agent, _, _ = make_agent([article()], [[item("1", "critical", [], -1)]], store=store)
    agent.refresh(["BTCUSDT"])
    assert agent.active_veto("ETHUSDT").is_global


# --- CryptoPanic --------------------------------------------------------------------------
V2_PAYLOAD = {
    "next": None,
    "results": [
        {
            "id": 101,
            "kind": "news",
            "title": "SEC demanda a X",
            "published_at": "2026-11-07T11:50:00Z",
            "source": {"title": "CoinDesk", "domain": "coindesk.com"},
            "instruments": [{"code": "btc", "title": "Bitcoin"}],
            "original_url": "https://example.com/a",
        },
        {
            "id": 102,
            "title": "v1",
            "created_at": "2026-11-07T11:00:00Z",
            "currencies": [{"code": "ETH"}, {"title": "sin código"}],
            "url": "https://cryptopanic.com/news/102",
        },
        {"id": 103},  # sin título: se ignora
    ],
}


@respx.mock
def test_cryptopanic_fetch_posts():
    route = respx.get(POSTS_URL).mock(return_value=httpx.Response(200, json=V2_PAYLOAD))
    c = CryptoPanicClient("tok")
    posts = c.fetch_posts(["eth", "BTC"], filter="important")
    assert [p.id for p in posts] == ["101", "102"]
    assert posts[0].currencies == ("BTC",) and posts[0].source == "CoinDesk"
    assert posts[0].url == "https://example.com/a"
    assert posts[1].currencies == ("ETH",) and posts[1].published_at.hour == 11
    params = route.calls.last.request.url.params
    assert params["auth_token"] == "tok" and params["currencies"] == "BTC,ETH"
    assert (
        params["kind"] == "news" and params["filter"] == "important" and params["public"] == "true"
    )
    c.close()


@respx.mock
def test_cryptopanic_minimal_params_and_from_env():
    route = respx.get("http://news.local/v2/posts/").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    c = CryptoPanicClient.from_env(
        {"CRYPTOPANIC_TOKEN": "t", "CRYPTOPANIC_BASE_URL": "http://news.local/v2/"}
    )
    assert c.fetch_posts(public=False) == []
    params = route.calls.last.request.url.params
    assert "currencies" not in params and "public" not in params and "filter" not in params
    assert CryptoPanicClient("t", plan="growth").base_url == "https://cryptopanic.com/api/growth/v2"


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(403, json={}),
        httpx.Response(200, text="<html>"),
        httpx.Response(200, json={"detail": "x"}),
        httpx.ConnectError("down"),
    ],
)
def test_cryptopanic_errors(response):
    route = respx.get(POSTS_URL)
    if isinstance(response, Exception):
        route.mock(side_effect=response)
    else:
        route.mock(return_value=response)
    with pytest.raises(NewsAPIError):
        CryptoPanicClient("tok").fetch_posts(["BTC"])


def test_cryptopanic_requires_token():
    with pytest.raises(ValueError):
        CryptoPanicClient.from_env({})


@respx.mock
def test_agent_with_cryptopanic_fail_open():
    respx.get(POSTS_URL).mock(return_value=httpx.Response(500))
    agent = NewsAgent(FakeLLM(), CryptoPanicClient("tok"))
    assert agent.refresh(["BTCUSDT"]).failed_open


def test_news_agent_from_env():
    agent = news_agent_from_env(
        {"LLM_PROVIDER": "fake", "CRYPTOPANIC_TOKEN": "t", "NEWS_VETO_HIGH_HOURS": "2"}
    )
    assert isinstance(agent.llm, FakeLLM)
    assert isinstance(agent.store, InMemoryVetoStore)
    assert agent.policy == VetoPolicy(high_duration=timedelta(hours=2))
