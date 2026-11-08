"""Sobre y fuentes de ``tm:signals`` (#57) con fakeredis."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import fakeredis
import pytest
from pydantic import ValidationError
from tm_agents.reference.signals import (
    SIGNALS_STREAM,
    InMemorySignalSource,
    RedisStreamSignalSource,
    SignalEnvelope,
    parse_entry,
    publish_signal,
)
from tm_shared.contracts import Direction, Horizon, Signal, Timeframe

T0 = datetime(2026, 11, 1, tzinfo=UTC)


def make_signal(source: str = "trend", **metadata) -> Signal:
    return Signal(
        symbol="BTCUSDT",
        direction=Direction.LONG,
        confidence=0.5,
        horizon=Horizon.MEDIUM,
        timeframe=Timeframe.D1,
        source=source,
        generated_at=T0,
        metadata=metadata,
    )


def test_envelope_defaults_from_signal():
    env = SignalEnvelope(signal=make_signal("Trend", last_close=64000.5))
    assert env.strategy == "trend"
    assert env.close == 64000.5
    assert env.ts == T0
    assert env.symbol == "BTCUSDT"


def test_envelope_explicit_fields_and_validation():
    close_at = T0 + timedelta(hours=1)
    env = SignalEnvelope(strategy="kronos", signal=make_signal(), close=10, bar_close_at=close_at)
    assert env.close == 10 and env.ts == close_at
    assert SignalEnvelope(signal=make_signal(last_close="x")).close is None
    with pytest.raises(ValidationError):
        SignalEnvelope(signal=make_signal(), bar_close_at=datetime(2026, 1, 1))
    with pytest.raises(ValidationError):
        SignalEnvelope(signal=make_signal(), close=-1)
    with pytest.raises(ValidationError):
        SignalEnvelope(signal=make_signal(), strategy="Bad Name")


def test_in_memory_source_drains():
    src = InMemorySignalSource([SignalEnvelope(signal=make_signal(), close=1)])
    src.push(SignalEnvelope(signal=make_signal(), close=2))
    assert len(src) == 2
    assert [e.close for e in src.poll()] == [1, 2]
    assert src.poll() == []


def test_parse_entry_bytes_and_invalid():
    env = SignalEnvelope(signal=make_signal(), close=5)
    assert parse_entry({b"data": env.to_json().encode()}) == env
    assert parse_entry({"data": "{not json"}) is None
    assert parse_entry({}) is None


def test_redis_source_reads_only_new_by_default():
    r = fakeredis.FakeRedis()
    publish_signal(r, SignalEnvelope(signal=make_signal(), close=1))  # antes de arrancar
    src = RedisStreamSignalSource(r)
    assert src.poll() == []
    entry_id = publish_signal(r, SignalEnvelope(signal=make_signal(), close=2))
    r.xadd(SIGNALS_STREAM, {"data": "basura"})
    got = src.poll()
    assert [e.close for e in got] == [2]
    assert src.invalid == 1
    assert src.last_id > entry_id


def test_redis_source_empty_stream_and_checkpoint_resume():
    r = fakeredis.FakeRedis()
    src = RedisStreamSignalSource(r, checkpoint_key="ck")
    assert src.last_id == "0-0"
    publish_signal(r, SignalEnvelope(signal=make_signal(), close=1))
    assert len(src.poll()) == 1
    src.commit()
    publish_signal(r, SignalEnvelope(signal=make_signal(), close=2))
    # un reinicio retoma desde el checkpoint, sin perder lo que llegó mientras tanto
    again = RedisStreamSignalSource(r, checkpoint_key="ck")
    assert [e.close for e in again.poll()] == [2]
    RedisStreamSignalSource(r).commit()  # sin llave no hace nada
