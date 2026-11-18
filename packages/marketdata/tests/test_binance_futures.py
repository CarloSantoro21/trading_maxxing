"""Tests del cliente Binance USD-M (funding + open interest).

Usa ``httpx.MockTransport`` para servir respuestas sin red y ``no_sleep`` para que
los reintentos no duerman de verdad.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
from tm_marketdata.binance_futures import (
    BINANCE_FUTURES_BASE_URL,
    BinanceFuturesClient,
    BinanceFuturesError,
    DerivativesUnavailable,
)


def _client(handler, *, sleep=None, max_retries=3) -> BinanceFuturesClient:
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url=BINANCE_FUTURES_BASE_URL)
    kwargs = {"http_client": http, "max_retries": max_retries, "env": {}}
    if sleep is not None:
        kwargs["sleep"] = sleep
    return BinanceFuturesClient(**kwargs)


def test_get_funding_rate_happy_path():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/fapi/v1/premiumIndex"
        assert request.url.params["symbol"] == "BTCUSDT"
        return httpx.Response(
            200,
            json={
                "symbol": "BTCUSDT",
                "markPrice": "68000.50",
                "indexPrice": "67990.10",
                "lastFundingRate": "0.00010000",
                "nextFundingTime": 1_767_240_000_000,  # 2026-01-01 04:00 UTC
                "time": 1_767_225_600_000,
            },
        )

    client = _client(handler)
    fr = client.get_funding_rate("btcusdt")
    assert fr.symbol == "BTCUSDT"
    assert fr.funding_rate == Decimal("0.00010000")
    assert fr.mark_price == Decimal("68000.50")
    assert fr.funding_time.isoformat().startswith("2026-01-01T04:00")


def test_get_funding_history_happy_path():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/fapi/v1/fundingRate"
        assert request.url.params["symbol"] == "ETHUSDT"
        assert request.url.params["limit"] == "2"
        return httpx.Response(
            200,
            json=[
                {
                    "symbol": "ETHUSDT",
                    "fundingRate": "0.00005",
                    "fundingTime": 1_767_225_600_000,
                    "markPrice": "3500.00",
                },
                {
                    "symbol": "ETHUSDT",
                    "fundingRate": "-0.00001",
                    "fundingTime": 1_767_254_400_000,
                },
            ],
        )

    rows = _client(handler).get_funding_history("ETHUSDT", limit=2)
    assert [r.funding_rate for r in rows] == [Decimal("0.00005"), Decimal("-0.00001")]
    assert rows[0].mark_price == Decimal("3500.00")
    assert rows[1].mark_price is None


def test_get_open_interest_happy_path():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/fapi/v1/openInterest"
        return httpx.Response(
            200,
            json={
                "openInterest": "12345.678",
                "symbol": "BTCUSDT",
                "time": 1_767_225_600_000,
            },
        )

    oi = _client(handler).get_open_interest("BTCUSDT")
    assert oi.symbol == "BTCUSDT"
    assert oi.open_interest == Decimal("12345.678")
    assert oi.notional_usd is None
    assert oi.ts.isoformat().startswith("2026-01-01T00:00")


def test_get_open_interest_history_happy_path():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/futures/data/openInterestHist"
        assert request.url.params["period"] == "1h"
        return httpx.Response(
            200,
            json=[
                {
                    "symbol": "BTCUSDT",
                    "sumOpenInterest": "100.5",
                    "sumOpenInterestValue": "6850000.75",
                    "timestamp": 1_767_225_600_000,
                },
                {
                    "symbol": "BTCUSDT",
                    "sumOpenInterest": "101.0",
                    "sumOpenInterestValue": "6900000.00",
                    "timestamp": "1767229200000",
                },
            ],
        )

    rows = _client(handler).get_open_interest_history("BTCUSDT", period="1h", limit=2)
    assert len(rows) == 2
    assert rows[0].open_interest == Decimal("100.5")
    assert rows[0].notional_usd == Decimal("6850000.75")
    assert rows[1].open_interest == Decimal("101.0")


def test_retry_on_429_then_success(no_sleep):
    sleep, calls = no_sleep
    responses = iter(
        [
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(
                200,
                json={
                    "symbol": "BTCUSDT",
                    "markPrice": "68000",
                    "lastFundingRate": "0.0001",
                    "nextFundingTime": 1_767_240_000_000,
                    "time": 1_767_225_600_000,
                },
            ),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return next(responses)

    fr = _client(handler, sleep=sleep).get_funding_rate("BTCUSDT")
    assert fr.symbol == "BTCUSDT"
    assert calls == [2.0]  # Respetó Retry-After antes del segundo intento


def test_client_error_400_fails_fast(no_sleep):
    sleep, calls = no_sleep
    hits: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(1)
        return httpx.Response(400, json={"code": -1121, "msg": "Invalid symbol"})

    with pytest.raises(BinanceFuturesError) as info:
        _client(handler, sleep=sleep).get_funding_rate("NOPE")
    assert info.value.status_code == 400
    assert len(hits) == 1
    assert calls == []


def test_testnet_only_raises_without_hitting_network(monkeypatch):
    monkeypatch.setenv("BINANCE_SPOT_TESTNET_ONLY", "true")

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no debe pegarle a la red")

    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url=BINANCE_FUTURES_BASE_URL)
    client = BinanceFuturesClient(http_client=http)

    with pytest.raises(DerivativesUnavailable):
        client.get_funding_rate("BTCUSDT")
    with pytest.raises(DerivativesUnavailable):
        client.get_funding_history("BTCUSDT")
    with pytest.raises(DerivativesUnavailable):
        client.get_open_interest("BTCUSDT")
    with pytest.raises(DerivativesUnavailable):
        client.get_open_interest_history("BTCUSDT")
