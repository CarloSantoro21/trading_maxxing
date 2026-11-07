from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
from tm_agents.base import AgentContext, build_strategies
from tm_agents.strategies.trend import TrendAgent, closed_bars, donchian_states
from tm_shared.contracts import Direction, Timeframe


def daily(closes, start="2024-01-01"):
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


def ctx_for(bars, now=None):
    now = now or (bars.index[-1] + pd.Timedelta(days=1)).to_pydatetime()
    return AgentContext(symbol="BTCUSDT", now=now, bars={Timeframe.D1: bars})


def test_uptrend_is_fully_long():
    bars = daily(100 * 1.01 ** np.arange(300))
    sig = TrendAgent().analyze(ctx_for(bars))
    assert sig.direction is Direction.LONG
    assert sig.confidence == pytest.approx(1.0)
    assert sig.metadata["votes"] == {"20": 1, "50": 1, "100": 1, "200": 1}


def test_downtrend_long_only_is_flat():
    bars = daily(100 * 0.99 ** np.arange(300))
    sig = TrendAgent().analyze(ctx_for(bars))
    assert sig.direction is Direction.FLAT and sig.confidence == 0.0


def test_downtrend_with_shorts():
    bars = daily(100 * 0.99 ** np.arange(300))
    sig = TrendAgent(allow_short=True).analyze(ctx_for(bars))
    assert sig.direction is Direction.SHORT and sig.confidence == pytest.approx(1.0)


def test_range_is_flat():
    closes = 100 + np.tile([0.0, 0.5, -0.5, 0.2, -0.2], 60)
    sig = TrendAgent().analyze(ctx_for(daily(closes)))
    assert sig.direction is Direction.FLAT


def test_trailing_exit_on_reversal():
    up = 100 * 1.01 ** np.arange(120)
    down = up[-1] * 0.97 ** np.arange(1, 15)
    sig = TrendAgent(lookbacks=[20]).analyze(ctx_for(daily(np.r_[up, down])))
    assert sig.metadata["votes"] == {"20": 0}


def test_partial_vote():
    # tendencia larga con corrección reciente: los periodos cortos salen, los largos siguen
    up = 100 * 1.01 ** np.arange(250)
    dip = up[-1] * 0.985 ** np.arange(1, 12)
    sig = TrendAgent().analyze(ctx_for(daily(np.r_[up, dip])))
    assert sig.direction is Direction.LONG
    assert 0 < sig.confidence < 1


def test_no_lookahead_ignores_unclosed_bar():
    bars = daily(100 * 1.01 ** np.arange(60))
    spike = bars.copy()
    spike.iloc[-1, spike.columns.get_loc("close")] = 1.0  # vela aún abierta en `now`
    now = bars.index[-1] + pd.Timedelta(hours=12)
    a = TrendAgent(lookbacks=[20]).analyze(ctx_for(bars, now.to_pydatetime()))
    b = TrendAgent(lookbacks=[20]).analyze(ctx_for(spike, now.to_pydatetime()))
    assert a == b and a.metadata["bars_used"] == 59


def test_closed_bars_with_naive_index():
    bars = daily([1, 2, 3])
    bars.index = bars.index.tz_localize(None)
    out = closed_bars(bars, pd.Timestamp("2024-01-03", tz="UTC"), pd.Timedelta(days=1))
    assert len(out) == 2


def test_not_enough_history_is_flat():
    sig = TrendAgent().analyze(ctx_for(daily([100, 101, 102])))
    assert sig.direction is Direction.FLAT and sig.metadata["annualized_vol"] is not None


def test_volatility_metadata():
    rng = np.random.default_rng(0)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 400)))
    sig = TrendAgent().analyze(ctx_for(daily(closes)))
    assert sig.metadata["annualized_vol"] == pytest.approx(0.02 * np.sqrt(365), rel=0.35)


def test_donchian_states_shape():
    s = donchian_states(np.ones(5), np.ones(5), np.ones(5), 3, False)
    assert s.tolist() == [0, 0, 0, 0, 0]


@pytest.mark.parametrize("bad", [[], [1], [0, 20]])
def test_invalid_lookbacks(bad):
    with pytest.raises(ValueError):
        TrendAgent(lookbacks=bad)


def test_registered_and_buildable():
    agents = build_strategies({"trend": {"lookbacks": [10, 30]}})
    assert isinstance(agents[0], TrendAgent) and agents[0].lookbacks == [10, 30]


def test_signal_timestamp_is_now():
    bars = daily(100 * 1.01 ** np.arange(30))
    now = datetime(2024, 2, 1, tzinfo=UTC)
    assert TrendAgent(lookbacks=[5]).analyze(ctx_for(bars, now)).generated_at == now
