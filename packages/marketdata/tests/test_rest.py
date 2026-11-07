import httpx
import pytest
import respx
from tm_marketdata.http import BinanceHTTPError, get_with_retries
from tm_marketdata.rest import BASE_URL, KLINES_PATH, BinanceRestClient

from .conftest import load_json

URL = BASE_URL + KLINES_PATH
START = 1_767_225_600_000  # 2026-01-01 00:00 UTC
END = START + 5 * 60_000


@respx.mock
def test_klines_paginates(no_sleep):
    sleep, _ = no_sleep
    route = respx.get(URL).mock(
        side_effect=[
            httpx.Response(200, json=load_json("klines_btcusdt_1m_page1.json")),
            httpx.Response(200, json=load_json("klines_btcusdt_1m_page2.json")),
        ]
    )
    client = BinanceRestClient(page_limit=3, sleep=sleep)
    rows = client.klines("BTCUSDT", "1m", START, END)
    assert len(rows) == 5
    assert route.call_count == 2
    second = route.calls[1].request.url.params
    assert second["startTime"] == str(START + 3 * 60_000)
    assert second["endTime"] == str(END - 1)
    assert second["limit"] == "3"


@respx.mock
def test_klines_empty_batch_stops():
    respx.get(URL).mock(return_value=httpx.Response(200, json=[]))
    assert BinanceRestClient().klines("BTCUSDT", "1m", START, END) == []


@respx.mock
def test_retries_on_429_and_5xx(no_sleep):
    sleep, calls = no_sleep
    respx.get(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(503),
            httpx.ConnectError("boom"),
            httpx.Response(200, json=load_json("klines_btcusdt_1m_page2.json")),
        ]
    )
    rows = BinanceRestClient(sleep=sleep, backoff=0.5).klines("BTCUSDT", "1m", START, END)
    assert len(rows) == 2
    assert calls == [7.0, 1.0, 2.0]  # Retry-After, luego backoff exponencial


@respx.mock
def test_gives_up_after_max_retries(no_sleep):
    sleep, calls = no_sleep
    respx.get(URL).mock(return_value=httpx.Response(500))
    with pytest.raises(BinanceHTTPError) as info:
        BinanceRestClient(sleep=sleep, max_retries=2).klines("BTCUSDT", "1m", START, END)
    assert info.value.status_code == 500
    assert len(calls) == 2


@respx.mock
def test_network_error_exhausted(no_sleep):
    sleep, _ = no_sleep
    respx.get(URL).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(BinanceHTTPError, match="error de red"):
        BinanceRestClient(sleep=sleep, max_retries=1).klines("BTCUSDT", "1m", START, END)


@respx.mock
def test_client_error_not_retried(no_sleep):
    sleep, calls = no_sleep
    route = respx.get(URL).mock(return_value=httpx.Response(400, json={"code": -1121}))
    with pytest.raises(BinanceHTTPError, match="400"):
        BinanceRestClient(sleep=sleep).klines("NOPE", "1m", START, END)
    assert route.call_count == 1
    assert calls == []


@respx.mock
def test_throttles_when_weight_is_high(no_sleep):
    sleep, calls = no_sleep
    respx.get(URL).mock(
        side_effect=[
            httpx.Response(
                200,
                json=load_json("klines_btcusdt_1m_page1.json"),
                headers={"X-MBX-USED-WEIGHT-1M": "5900"},
            ),
            httpx.Response(200, json=load_json("klines_btcusdt_1m_page2.json")),
        ]
    )
    client = BinanceRestClient(page_limit=3, sleep=sleep, clock=lambda: 1_000_065.0)
    client.klines("BTCUSDT", "1m", START, END)
    assert calls == [16.0]  # espera al siguiente minuto (+1 s de margen)
    assert client.used_weight == 0


def test_retry_after_invalid_falls_back_to_backoff(no_sleep):
    sleep, calls = no_sleep
    responses = iter([httpx.Response(429, headers={"Retry-After": "x"}), httpx.Response(200)])
    transport = httpx.MockTransport(lambda request: next(responses))
    with httpx.Client(transport=transport) as client:
        response = get_with_retries(client, "https://example.test", sleep=sleep, backoff=3)
    assert response.status_code == 200
    assert calls == [3]
