"""Formato único de ``tm:signals`` (:mod:`tm_agents.signals`) y sus fuentes, con fakeredis."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import fakeredis
import pytest
from pydantic import ValidationError
from tm_agents.registry import StrategyRecord
from tm_agents.signals import (
    SIGNALS_STREAM,
    ListSignalSource,
    RedisSignalSink,
    RedisSignalSource,
    SignalEvent,
    StrategySnapshot,
    parse_signal,
    publish_signal,
)
from tm_agents.strategy_stats import StrategyState
from tm_shared.contracts import Direction, Horizon, Signal, Timeframe

T0 = datetime(2026, 11, 1, tzinfo=UTC)


def make_signal(source: str = "trend", at: datetime = T0) -> Signal:
    return Signal(
        symbol="BTCUSDT",
        direction=Direction.LONG,
        confidence=0.5,
        horizon=Horizon.MEDIUM,
        timeframe=Timeframe.D1,
        source=source,
        generated_at=at,
    )


def ev(price: float = 100.0, hours: int = 0, source: str = "trend") -> SignalEvent:
    return SignalEvent.from_signal(make_signal(source, T0 + timedelta(hours=hours)), price)


def test_from_signal_defaults():
    e = SignalEvent.from_signal(make_signal("Trend"), 64000.5)
    assert e.strategy == "trend" and e.symbol == "BTCUSDT"
    assert e.id == "trend:BTCUSDT:2026-11-01T00:00:00Z"
    assert e.bar_close == T0 and e.price == Decimal("64000.5")
    assert e.state is StrategyState.ACTIVE and not e.is_shadow
    assert e.stop_loss < e.price < e.take_profit and e.hard_stop is False
    assert e.veto is None and e.sim is None and e.catalog == ()
    shadow = SignalEvent.from_signal(
        make_signal(), 10, strategy="kronos", state=StrategyState.SHADOW, bar_close=T0
    )
    assert shadow.is_shadow and shadow.strategy == "kronos"


def test_event_validation():
    with pytest.raises(ValidationError):
        SignalEvent.from_signal(make_signal(), -1)
    with pytest.raises(ValidationError):
        SignalEvent.from_signal(make_signal(), 1, strategy="Bad Name")
    with pytest.raises(ValidationError):
        SignalEvent.from_signal(make_signal(), 1, bar_close=datetime(2026, 1, 1))


def test_parse_signal_bytes_and_invalid():
    e = ev(5)
    assert parse_signal({b"data": e.to_json().encode()}) == e
    assert parse_signal({"data": e.to_json()}) == e
    assert parse_signal({"data": "{not json"}) is None
    assert parse_signal({}) is None


def test_list_source_orders_push_and_releases_by_time():
    src = ListSignalSource([ev(2, hours=2)])
    src.push(ev(1, hours=1))
    src.push(ev(3, hours=3))
    assert len(src) == 3
    assert [e.price for e in src.poll(T0 + timedelta(hours=2))] == [1, 2]
    assert [e.price for e in src.poll()] == [3]
    assert src.poll() == []


def test_redis_source_reads_only_new_by_default():
    r = fakeredis.FakeRedis()
    publish_signal(r, ev(1))  # antes de arrancar
    src = RedisSignalSource(r)
    assert src.poll() == []
    entry_id = RedisSignalSink(r).publish(ev(2))
    r.xadd(SIGNALS_STREAM, {"data": "basura"})
    got = src.poll()
    assert [e.price for e in got] == [2]
    assert src.invalid == 1
    assert src.last_id > entry_id


def test_publish_without_trim_and_to_another_stream():
    r = fakeredis.FakeRedis()
    for i in range(5):
        publish_signal(r, ev(i + 1), maxlen=None)
    assert r.xlen(SIGNALS_STREAM) == 5
    publish_signal(r, ev(9), stream="tm:otro")
    assert r.xlen("tm:otro") == 1


def test_redis_source_empty_stream_and_checkpoint_resume():
    r = fakeredis.FakeRedis()
    src = RedisSignalSource(r, checkpoint_key="ck")
    assert src.last_id == "0-0"
    publish_signal(r, ev(1))
    assert len(src.poll()) == 1
    src.commit()
    publish_signal(r, ev(2))
    # un reinicio retoma desde el checkpoint, sin perder lo que llegó mientras tanto
    again = RedisSignalSource(r, checkpoint_key="ck")
    assert [e.price for e in again.poll()] == [2]
    RedisSignalSource(r).commit()  # sin llave no hace nada


def test_snapshot_reads_both_lab_report_formats():
    """El p95 se lee de los mismos formatos que usa el LifecycleMonitor (DrawdownBand)."""
    nested = {"montecarlo": {"max_drawdown": {"p95": 0.3, "p99": 0.4}}, "config": {}}
    flat = {"max_drawdown_p95": 0.25, "max_drawdown_p99": 0.35}
    for report, p95 in ((nested, 0.3), (flat, 0.25)):
        record = StrategyRecord(
            name="trend",
            state=StrategyState.ACTIVE,
            state_changed_at=T0,
            created_at=T0,
            lab_report=report,
        )
        snap = StrategySnapshot.from_record(record)
        assert snap.max_drawdown_p95 == p95
        assert snap.sensitivity is not None  # curva medida de "trend" (KNOWN_SENSITIVITY)
