"""Libro de sleeves, sobres de decisión y fuentes (programada y stream de Redis)."""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

import fakeredis
import pandas as pd
import pytest
from tm_engine.decisions import (
    DECISIONS_STREAM,
    DecisionEnvelope,
    RedisStreamDecisionSource,
    ScheduledDecisionSource,
    parse_entry,
    publish_decision,
    utc,
)
from tm_engine.instruments import bar_type, bars_from_frame, spot_instrument
from tm_engine.sleeves import DEFAULT_SLEEVE, SleeveLedger, sleeve_from_tags, sleeve_tag
from tm_shared.contracts import Direction, Horizon, OrderSide, Rationale, Signal, Timeframe

# --- sleeves -----------------------------------------------------------------------------


def test_sleeve_tags_roundtrip():
    assert sleeve_from_tags([sleeve_tag("trend"), "role:entry"]) == "trend"
    assert sleeve_from_tags(None) == DEFAULT_SLEEVE
    assert sleeve_from_tags(["role:sl"]) == DEFAULT_SLEEVE


def test_ledger_average_cost_and_realized_pnl():
    ledger = SleeveLedger()
    buy = {"sleeve": "trend", "symbol": "BTCUSDT", "side": OrderSide.BUY}
    assert ledger.apply_fill(**buy, quantity=Decimal(1), price=Decimal(100)) == 0
    ledger.apply_fill(**buy, quantity=Decimal(1), price=Decimal(110), commission=Decimal("0.5"))
    assert ledger.get("trend").positions["BTCUSDT"].avg_price == Decimal(105)
    realized = ledger.apply_fill(
        sleeve="trend",
        symbol="BTCUSDT",
        side=OrderSide.SELL,
        quantity=Decimal(3),  # más de lo que tiene: se recorta a 2
        price=Decimal(120),
        commission=Decimal(1),
    )
    assert realized == Decimal(29)
    stats = ledger.get("trend")
    assert stats.realized_pnl == Decimal("28.5")
    assert stats.closed_trades == 1
    assert ledger.quantity("trend", "BTCUSDT") == 0
    assert ledger.quantity("otro", "BTCUSDT") == 0
    report = ledger.to_dict()
    assert report["trend"]["positions"] == {}
    assert report["trend"]["fills"] == 3
    assert set(ledger.sleeves) == {"trend"}


def test_ledger_keeps_sleeves_apart():
    ledger = SleeveLedger()
    for sleeve, qty in (("a", 1), ("b", 2)):
        ledger.apply_fill(
            sleeve=sleeve,
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=Decimal(qty),
            price=Decimal(10),
        )
    assert ledger.quantity("a", "BTCUSDT") == 1
    assert ledger.to_dict()["b"]["positions"]["BTCUSDT"] == {"quantity": "2", "avg_price": "10"}


# --- sobres --------------------------------------------------------------------------------


def test_envelope_defaults_sleeve_from_first_signal(envelope):
    base = envelope().decision
    signal = Signal(
        symbol="BTCUSDT",
        direction=Direction.LONG,
        confidence=0.7,
        horizon=Horizon.MEDIUM,
        timeframe=Timeframe.M30,
        source="Kronos",
    )
    with_signal = base.model_copy(update={"rationale": Rationale(signals=[signal])})
    assert DecisionEnvelope(decision=with_signal).sleeve == "kronos"
    assert DecisionEnvelope(decision=base).sleeve == DEFAULT_SLEEVE


def test_envelope_rejects_bad_sleeve(envelope):
    with pytest.raises(ValueError):
        DecisionEnvelope(sleeve="Con Espacios", decision=envelope().decision)


def test_parse_entry_accepts_bytes_and_rejects_garbage(envelope):
    env = envelope()
    assert parse_entry({b"data": env.to_json().encode()}) == env
    assert parse_entry({"data": "no es json"}) is None
    assert parse_entry({"data": json.dumps({"sleeve": "x"})}) is None
    assert parse_entry({}) is None


def test_scheduled_source_releases_by_time(envelope, t0):
    source = ScheduledDecisionSource([envelope(minute=5), envelope(minute=1)])
    assert source.poll(t0) == []
    assert len(source.poll(t0 + timedelta(minutes=1))) == 1
    assert source.pending == 1
    assert len(source.poll(t0 + timedelta(minutes=10))) == 1
    assert source.pending == 0


def test_redis_stream_source_reads_only_new_entries(envelope):
    client = fakeredis.FakeRedis()
    publish_decision(client, envelope(sleeve="old"))
    source = RedisStreamDecisionSource(client)
    assert source.poll() == []
    publish_decision(client, envelope(sleeve="trend"))
    client.xadd(DECISIONS_STREAM, {"data": "roto"})
    got = source.poll()
    assert [e.sleeve for e in got] == ["trend"]
    assert source.poll() == []


def test_redis_stream_source_on_empty_stream_and_explicit_id(envelope):
    client = fakeredis.FakeRedis(decode_responses=True)
    source = RedisStreamDecisionSource(client)
    assert source.last_id == "0-0"
    publish_decision(client, envelope())
    assert len(source.poll()) == 1
    replay = RedisStreamDecisionSource(client, last_id="0-0")
    assert len(replay.poll()) == 1


def test_utc_helper(t0):
    assert utc(t0.replace(tzinfo=None)) == t0
    assert utc(t0) is t0


# --- instrumentos --------------------------------------------------------------------------


def test_spot_instrument_specs():
    btc = spot_instrument("BTCUSDT")
    assert btc.taker_fee == Decimal("0.001")
    assert btc.size_precision == 5
    assert spot_instrument("ETHUSDT").size_precision == 4
    with pytest.raises(ValueError):
        spot_instrument("SOLUSDT")


def test_bars_are_stamped_at_close(make_frame, t0):
    frame = make_frame([100, 101])
    bars = bars_from_frame(frame, spot_instrument("BTCUSDT"), Timeframe.M1)
    assert len(bars) == 2
    assert pd.Timestamp(bars[0].ts_event, tz="UTC") == pd.Timestamp(t0) + pd.Timedelta(minutes=1)
    assert bars[1].close.as_decimal() == Decimal("101.00")
    naive = frame.tz_localize(None)
    assert bars_from_frame(naive, spot_instrument("BTCUSDT"))[0].ts_event == bars[0].ts_event
    assert str(bar_type("ETHUSDT", "1d")) == "ETHUSDT.BINANCE-1-DAY-LAST-EXTERNAL"


def test_bars_from_arrow_backed_frame(make_frame):
    """Los frames de tm_marketdata vienen de Parquet (buffers de solo lectura)."""
    pytest.importorskip("pyarrow")
    frame = make_frame([100, 101, 102]).astype("float64[pyarrow]")
    bars = bars_from_frame(frame, spot_instrument("BTCUSDT"))
    assert [b.close.as_decimal() for b in bars] == [Decimal(100), Decimal(101), Decimal(102)]
