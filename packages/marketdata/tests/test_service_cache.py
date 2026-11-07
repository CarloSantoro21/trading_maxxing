import fakeredis
import httpx
import pandas as pd
import pytest
import redis
import respx
from tm_marketdata.cache import NullCache, RedisCache, bars_key, cache_from_env
from tm_marketdata.rest import BASE_URL, KLINES_PATH, BinanceRestClient
from tm_marketdata.schema import OHLCV, normalize_klines
from tm_marketdata.service import MarketDataService, missing_segments
from tm_marketdata.storage import ParquetStore

from .conftest import load_json

URL = BASE_URL + KLINES_PATH
PAGE1 = load_json("klines_btcusdt_1m_page1.json")
PAGE2 = load_json("klines_btcusdt_1m_page2.json")
T0 = pd.Timestamp("2026-01-01", tz="UTC")


def clock_at(ts: str):
    return lambda: pd.Timestamp(ts, tz="UTC")


def service(tmp_path, cache=None, now="2026-01-01 00:05:30", page_limit=1000):
    rest = BinanceRestClient(page_limit=page_limit, sleep=lambda _: None)
    return MarketDataService(
        ParquetStore(tmp_path), rest, cache or NullCache(), clock=clock_at(now)
    )


@respx.mock
def test_get_bars_from_rest_only_closed(tmp_path):
    # A las 00:05:30 la vela de 00:05 sigue abierta: Binance la mandaría, pero se descarta.
    open_bar = [*PAGE2[-1]]
    open_bar[0] = PAGE2[-1][0] + 60_000
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=PAGE1 + PAGE2 + [open_bar]))
    df = service(tmp_path).get_bars("BTC/USDT", "1m", T0, "2026-01-02")
    assert list(df.columns) == OHLCV
    assert len(df) == 5
    assert df.index[-1] == pd.Timestamp("2026-01-01 00:04", tz="UTC")
    # El endTime pedido se recorta a la última vela cerrada.
    assert route.calls[0].request.url.params["endTime"] == str(T0.value // 10**6 + 5 * 60_000 - 1)


def test_get_bars_from_disk_without_network(tmp_path):
    store = ParquetStore(tmp_path)
    store.write_year("BTCUSDT", "1m", 2026, normalize_klines(PAGE1 + PAGE2), {1})
    svc = service(tmp_path)
    with respx.mock:  # cualquier petición fallaría
        df = svc.get_bars("BTCUSDT", "1m", T0, T0 + pd.Timedelta(minutes=5), include_extra=True)
    assert len(df) == 5
    assert "quote_volume" in df.columns


@respx.mock
def test_get_bars_fills_tail_from_rest(tmp_path):
    store = ParquetStore(tmp_path)
    store.write_year("BTCUSDT", "1m", 2026, normalize_klines(PAGE1), {1})
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=PAGE2))
    df = service(tmp_path).get_bars("BTCUSDT", "1m", T0)
    assert len(df) == 5
    params = route.calls[0].request.url.params
    assert params["startTime"] == str(T0.value // 10**6 + 3 * 60_000)


def test_get_bars_empty_range(tmp_path):
    svc = service(tmp_path)
    assert svc.get_bars("BTCUSDT", "1m", "2030-01-01", "2030-02-01").empty
    assert svc.get_bars("BTCUSDT", "1m", "2026-01-01 00:03", "2026-01-01 00:01").empty


@respx.mock
def test_get_bars_rest_returns_nothing(tmp_path):
    respx.get(URL).mock(return_value=httpx.Response(200, json=[]))
    assert service(tmp_path).get_bars("BTCUSDT", "1m", T0).empty


def test_missing_segments():
    step = 60_000
    local = normalize_klines(PAGE1)  # 00:00, 00:01, 00:02
    start = T0.value // 10**6
    assert missing_segments(local.iloc[:0], start, start + 5 * step, step) == [
        (start, start + 5 * step)
    ]
    assert missing_segments(local, start, start + 3 * step, step) == []
    assert missing_segments(local.iloc[1:], start, start + 3 * step, step) == [
        (start, start + step)
    ]
    assert missing_segments(local, start, start + 5 * step, step) == [
        (start + 3 * step, start + 5 * step)
    ]


@respx.mock
def test_redis_cache_hit_avoids_rest(tmp_path):
    cache = RedisCache(fakeredis.FakeRedis())
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=PAGE1 + PAGE2))
    svc = service(tmp_path, cache=cache)
    first = svc.get_bars("BTCUSDT", "1m", T0)
    second = svc.get_bars("BTCUSDT", "1m", T0)
    assert route.call_count == 1
    pd.testing.assert_frame_equal(first, second, check_freq=False)

    key = bars_key("BTCUSDT", "1m", T0.value // 10**6, T0.value // 10**6 + 5 * 60_000)
    ttl = cache._client.ttl(key)
    assert 0 < ttl <= 30  # TTL de 1m


def test_redis_cache_tolerates_errors(caplog):
    class Broken:
        def get(self, key):
            raise redis.ConnectionError("down")

        def set(self, *args, **kwargs):
            raise redis.ConnectionError("down")

    cache = RedisCache(Broken())
    assert cache.get("k") is None
    cache.set("k", normalize_klines(PAGE1), 10)
    assert "Redis no disponible" in caplog.text


def test_null_cache():
    cache = NullCache()
    cache.set("k", normalize_klines(PAGE1), 10)
    assert cache.get("k") is None


def test_cache_from_env():
    assert isinstance(cache_from_env({}), NullCache)
    assert isinstance(cache_from_env({"REDIS_URL": "redis://localhost:6379/0"}), RedisCache)


def test_service_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    svc = MarketDataService(data_dir=tmp_path)
    assert svc.store.root == tmp_path
    assert isinstance(svc.cache, NullCache)
    with pytest.raises(ValueError):
        svc.get_bars("BTCUSDT", "3m", T0)
