from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
from tm_agents.base import AgentContext, build_strategies
from tm_agents.strategies.trend import (
    TrendAgent,
    closed_bars,
    donchian_states,
    realized_vol,
    sized_exposure,
    vol_scale,
)
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


# --- volatility targeting (#83) ------------------------------------------------------------


def noisy_uptrend(n=300, vol=0.04, seed=0):
    rng = np.random.default_rng(seed)
    return 100 * np.exp(np.cumsum(0.01 + rng.normal(0, vol, n)))


def test_realized_vol_uses_only_closed_bars():
    closes = noisy_uptrend(120)
    vol = realized_vol(closes, 30)
    assert np.isnan(vol[:30]).all() and np.isfinite(vol[30:]).all()
    # cambiar el futuro no cambia el pasado (sin look-ahead)
    shocked = closes.copy()
    shocked[80:] *= 3
    np.testing.assert_array_equal(realized_vol(shocked, 30)[:80], vol[:80])
    expected = np.std(np.diff(np.log(closes[-31:])), ddof=1) * np.sqrt(365)
    assert vol[-1] == pytest.approx(expected)
    with pytest.raises(ValueError):
        realized_vol(closes, 1)


def test_vol_scale_never_levers():
    scale = vol_scale(np.array([np.nan, 0.0, 0.2, 0.4, 0.8, 1.6]), 0.4)
    assert scale.tolist() == [0.0, 1.0, 1.0, 1.0, 0.5, 0.25]
    assert (scale <= 1).all()


def test_sized_exposure_band():
    state = np.array([0, 1, 1, 1, 1, 1, 0, 1])
    scale = np.array([0.9, 0.5, 0.55, 0.62, 0.55, 0.30, 0.3, 0.8])
    out = sized_exposure(state, scale, band=0.10)
    # entra a 0.5; 0.55 queda dentro de la banda; 0.62 y 0.30 la rebasan
    assert out.tolist() == [0.0, 0.5, 0.5, 0.62, 0.62, 0.30, 0.0, 0.8]
    assert sized_exposure(state, scale, band=0.0).tolist()[1:6] == [0.5, 0.55, 0.62, 0.55, 0.3]
    # cambiar de lado (largo -> corto) también rebalancea
    assert sized_exposure(np.array([1, -1]), np.array([0.5, 0.52]), 0.1).tolist() == [0.5, 0.52]
    with pytest.raises(ValueError):
        sized_exposure(state, scale, band=1.0)


def test_vol_target_scales_confidence_and_metadata():
    bars = daily(noisy_uptrend(300, vol=0.04))
    plain = TrendAgent().analyze(ctx_for(bars))
    sized = TrendAgent(vol_target=0.40).analyze(ctx_for(bars))
    assert plain.direction is sized.direction is Direction.LONG
    md = sized.metadata
    assert md["vote"] == pytest.approx(plain.confidence)
    assert md["exposure"] == pytest.approx(sized.confidence)
    assert 0 < sized.confidence < plain.confidence  # vol ~76 % > 40 %: recorta
    assert md["vol_scale"] == pytest.approx(min(1, 0.40 / md["realized_vol"]))
    assert md["vol_target"] == 0.40 and md["vol_window"] == 30 and md["rebalance_band"] == 0.1
    assert set(md["exposures"]) == {"20", "50", "100", "200"}
    # la exposición de cada periodo está a menos de una banda de la escala actual
    for e in md["exposures"].values():
        assert e == 0 or abs(e - md["vol_scale"]) <= 0.10 + 1e-12
    assert "exposure" not in plain.metadata


def test_vol_target_above_realized_vol_is_unchanged():
    bars = daily(noisy_uptrend(300, vol=0.005))
    plain = TrendAgent().analyze(ctx_for(bars))
    sized = TrendAgent(vol_target=2.0).analyze(ctx_for(bars))
    assert sized.confidence == pytest.approx(plain.confidence)
    assert sized.metadata["vol_scale"] == 1.0


def test_vol_target_without_history_does_not_trade():
    bars = daily(100 * 1.05 ** np.arange(12))
    sig = TrendAgent(lookbacks=[5], vol_target=0.4, vol_window=20).analyze(ctx_for(bars))
    assert sig.direction is Direction.LONG and sig.confidence == 0.0
    assert sig.metadata["realized_vol"] is None


def test_vol_target_no_lookahead():
    bars = daily(noisy_uptrend(200))
    spike = bars.copy()
    spike.iloc[-1, spike.columns.get_loc("close")] *= 5  # vela aún abierta en `now`
    now = (bars.index[-1] + pd.Timedelta(hours=12)).to_pydatetime()
    a = TrendAgent(vol_target=0.4).analyze(ctx_for(bars, now))
    b = TrendAgent(vol_target=0.4).analyze(ctx_for(spike, now))
    assert a == b


@pytest.mark.parametrize(
    "kwargs", [{"vol_target": 0.0}, {"vol_window": 1}, {"rebalance_band": -0.1}]
)
def test_invalid_vol_params(kwargs):
    with pytest.raises(ValueError):
        TrendAgent(**kwargs)


def test_vol_params_are_buildable():
    (agent,) = build_strategies({"trend": {"vol_target": 0.5, "vol_window": 60}})
    assert agent.vol_target == 0.5 and agent.vol_window == 60
    assert agent.params["vol_target"] == 0.5
