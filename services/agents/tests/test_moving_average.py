from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
from tm_agents.base import AgentContext, build_strategies, registered_strategies
from tm_agents.strategies import MovingAverageAgent
from tm_shared.contracts import Direction, Horizon, Timeframe


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


def test_long_when_price_above_both_smas():
    bars = daily(100 * 1.01 ** np.arange(150))
    sig = MovingAverageAgent().analyze(ctx_for(bars))
    assert sig.direction is Direction.LONG
    assert sig.confidence == pytest.approx(1.0)
    assert sig.horizon is Horizon.MEDIUM
    assert sig.timeframe is Timeframe.D1
    # ambas medias quedaron debajo del último cierre
    assert sig.metadata["ratio_50"] > 1.0
    assert sig.metadata["ratio_100"] > 1.0


def test_flat_when_price_below_both_smas():
    bars = daily(100 * 0.99 ** np.arange(150))
    sig = MovingAverageAgent().analyze(ctx_for(bars))
    assert sig.direction is Direction.FLAT
    assert sig.confidence == 0.0
    assert sig.metadata["ratio_50"] < 1.0
    assert sig.metadata["ratio_100"] < 1.0


def test_partial_vote_gives_half_confidence():
    # Construimos cierres de modo que el último cierre quede por encima de la SMA50
    # pero por debajo de la SMA100.
    # - Primeros 50 cierres: valor alto (120). Son los que arrastran la SMA100 hacia arriba.
    # - Siguientes 50 cierres (los 50 previos al último): valor bajo (80). Esos son los
    #   N=50 previos al último, su SMA50 vale 80.
    # - Último cierre: 100. Está sobre la SMA50 (80) pero bajo la SMA100
    #   ((50*120 + 50*80) / 100 = 100 ⇒ hacemos la primera mitad en 130 para que la
    #   SMA100 quede en 105 > 100).
    high = np.full(50, 130.0)
    low = np.full(50, 80.0)
    last = np.array([100.0])
    bars = daily(np.r_[high, low, last])
    sig = MovingAverageAgent().analyze(ctx_for(bars))
    assert sig.direction is Direction.LONG
    assert sig.confidence == pytest.approx(0.5)
    assert sig.metadata["ratio_50"] > 1.0  # 100 / 80
    assert sig.metadata["ratio_100"] < 1.0  # 100 / 105


def test_no_lookahead_future_bars_do_not_affect_signal():
    bars = daily(100 * 1.01 ** np.arange(150))
    # Ahora `now` cae un día después del cierre de la vela 120 (la 121 aún no cerró).
    now = (bars.index[120] + pd.Timedelta(hours=12)).to_pydatetime()
    base = MovingAverageAgent().analyze(ctx_for(bars, now))

    # Modificamos las velas futuras (índices > 120) con valores extremos.
    tampered = bars.copy()
    for col in ("open", "high", "low", "close"):
        tampered.iloc[121:, tampered.columns.get_loc(col)] = 1.0
    tampered_sig = MovingAverageAgent().analyze(ctx_for(tampered, now))

    assert base == tampered_sig


def test_insufficient_history_returns_flat():
    # 100 ventanas requiere al menos 101 cierres; con 80 debe quedar flat.
    bars = daily(100 * 1.01 ** np.arange(80))
    sig = MovingAverageAgent().analyze(ctx_for(bars))
    assert sig.direction is Direction.FLAT
    assert sig.confidence == 0.0
    assert sig.metadata["sma_50"] is None
    assert sig.metadata["sma_100"] is None
    assert sig.metadata["ratio_50"] is None
    assert sig.metadata["ratio_100"] is None


def test_metadata_keys_present_and_numeric():
    bars = daily(100 * 1.005 ** np.arange(150))
    sig = MovingAverageAgent().analyze(ctx_for(bars))
    md = sig.metadata
    assert set(md["windows"]) == {50, 100}
    assert md["bars_used"] == 150
    assert isinstance(md["last_close"], float)
    for n in (50, 100):
        assert isinstance(md[f"sma_{n}"], float)
        assert isinstance(md[f"ratio_{n}"], float)
        assert md[f"sma_{n}"] > 0
        assert md[f"ratio_{n}"] > 0


def test_registered_and_buildable():
    assert "moving_average" in registered_strategies()
    (agent,) = build_strategies({"moving_average": {"windows": [10, 20]}})
    assert isinstance(agent, MovingAverageAgent)
    assert agent.windows == [10, 20]


def test_signal_timestamp_is_now():
    bars = daily(100 * 1.01 ** np.arange(60))
    now = datetime(2024, 3, 1, tzinfo=UTC)
    sig = MovingAverageAgent(windows=[5, 10]).analyze(ctx_for(bars, now))
    assert sig.generated_at == now
    assert sig.source == "moving_average"


@pytest.mark.parametrize("bad", [[], [1], [0, 20], [50, 1]])
def test_invalid_windows(bad):
    with pytest.raises(ValueError):
        MovingAverageAgent(windows=bad)


def test_sma_uses_prior_closes_only():
    # Sanity-check numérico: con cierres conocidos verificamos que la SMA es de los N
    # cierres previos al último (no incluye la vela actual).
    closes = np.arange(1.0, 13.0)  # 1, 2, ..., 12; 12 velas, max_window=5 requiere 6 previos
    bars = daily(closes)
    sig = MovingAverageAgent(windows=[5]).analyze(ctx_for(bars))
    # últimos 5 cierres previos al último (12): [7, 8, 9, 10, 11] -> media 9
    assert sig.metadata["sma_5"] == pytest.approx(9.0)
    assert sig.metadata["last_close"] == pytest.approx(12.0)
    assert sig.metadata["ratio_5"] == pytest.approx(12.0 / 9.0)
    assert sig.direction is Direction.LONG
    assert sig.confidence == pytest.approx(1.0)
