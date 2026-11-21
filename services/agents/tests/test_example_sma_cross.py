"""Pruebas de ExampleSmaCrossAgent: regla, sin look-ahead, historia corta, metadata y registro.

Este archivo acompaña al ejemplo producido por la skill `.claude/skills/nueva-estrategia`.
Su objetivo es probar el scaffolding del flujo: si estas pruebas pasan, la plantilla de la
skill está funcionando.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tm_agents.base import AgentContext, build_strategies
from tm_agents.strategies.example_sma_cross import ExampleSmaCrossAgent, sma_cross_signal
from tm_shared.contracts import Direction, Timeframe


def daily(closes, start: str = "2024-01-01") -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    idx = pd.date_range(start, periods=len(closes), freq="D", tz="UTC", name="open_time")
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes * 1.01,
            "low": closes * 0.99,
            "close": closes,
            "volume": 1.0,
        },
        index=idx,
    )


def ctx_for(bars: pd.DataFrame, now=None) -> AgentContext:
    now = now or (bars.index[-1] + pd.Timedelta(days=1)).to_pydatetime()
    return AgentContext(symbol="BTCUSDT", now=now, bars={Timeframe.D1: bars})


def test_uptrend_is_long() -> None:
    bars = daily(100 * 1.01 ** np.arange(200))
    sig = ExampleSmaCrossAgent().analyze(ctx_for(bars))
    assert sig.direction is Direction.LONG
    assert sig.confidence > 0
    assert sig.metadata["sma_fast"] > sig.metadata["sma_slow"]
    assert sig.metadata["spread"] > 0


def test_downtrend_is_flat() -> None:
    bars = daily(100 * 0.99 ** np.arange(200))
    sig = ExampleSmaCrossAgent().analyze(ctx_for(bars))
    assert sig.direction is Direction.FLAT
    assert sig.confidence == 0.0


def test_no_lookahead_ignores_unclosed_bar() -> None:
    bars = daily(100 * 1.01 ** np.arange(80))
    spike = bars.copy()
    spike.iloc[-1, spike.columns.get_loc("close")] = 1.0  # vela aún abierta en `now`
    now = (bars.index[-1] + pd.Timedelta(hours=12)).to_pydatetime()
    a = ExampleSmaCrossAgent().analyze(ctx_for(bars, now))
    b = ExampleSmaCrossAgent().analyze(ctx_for(spike, now))
    assert a == b


def test_insufficient_history_is_flat() -> None:
    sig = ExampleSmaCrossAgent().analyze(ctx_for(daily([100, 101, 102, 103, 104])))
    assert sig.direction is Direction.FLAT
    assert sig.confidence == 0.0
    assert sig.metadata["reason"] == "historia insuficiente"
    assert sig.metadata["sma_fast"] is None
    assert sig.metadata["sma_slow"] is None


def test_metadata_has_documented_keys() -> None:
    bars = daily(100 * 1.01 ** np.arange(200))
    sig = ExampleSmaCrossAgent().analyze(ctx_for(bars))
    for key in ("bars_used", "last_close", "sma_fast", "sma_slow", "spread"):
        assert key in sig.metadata
    assert isinstance(sig.metadata["bars_used"], int)
    assert isinstance(sig.metadata["last_close"], float)


def test_confidence_is_capped_at_one() -> None:
    # fast muy por encima de slow: spread > 1, pero la confianza queda acotada en 1.
    closes = np.r_[np.full(50, 10.0), np.full(20, 100.0)]
    sig = ExampleSmaCrossAgent(fast=20, slow=50).analyze(ctx_for(daily(closes)))
    assert sig.direction is Direction.LONG
    assert sig.confidence == 1.0


def test_registered_and_buildable() -> None:
    agents = build_strategies({"example_sma_cross": {"fast": 10, "slow": 30}})
    assert isinstance(agents[0], ExampleSmaCrossAgent)
    assert agents[0].fast == 10 and agents[0].slow == 30


def test_pure_rule_matches_agent() -> None:
    closes = 100 * 1.01 ** np.arange(200)
    direction_int, fast, slow, spread = sma_cross_signal(closes, 20, 50)
    assert direction_int == 1
    assert fast > slow
    assert spread == pytest.approx((fast - slow) / slow)


def test_pure_rule_without_history_is_flat() -> None:
    direction_int, fast, slow, spread = sma_cross_signal(np.array([1.0, 2.0, 3.0]), 2, 5)
    assert direction_int == 0
    assert spread == 0.0
    assert np.isnan(fast) and np.isnan(slow)


@pytest.mark.parametrize(
    "kwargs",
    [{"fast": 1}, {"fast": 50, "slow": 50}, {"fast": 50, "slow": 20}],
)
def test_invalid_params_raise(kwargs) -> None:
    with pytest.raises(ValueError):
        ExampleSmaCrossAgent(**kwargs)


def test_pure_rule_invalid_params_raise() -> None:
    with pytest.raises(ValueError):
        sma_cross_signal(np.array([1.0, 2.0, 3.0]), 10, 5)
