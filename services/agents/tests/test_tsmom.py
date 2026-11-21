from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
from tm_agents.base import AgentContext, build_strategies
from tm_agents.strategies.tsmom import (
    HOLD_DAYS,
    TsmomAgent,
    _annualized_vol,
    _last_sunday_index,
    _tranche_long,
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


# --- regla básica -------------------------------------------------------------------------


def test_uptrend_sunday_is_long():
    # 2024-01-01 fue lunes, así que el día 7 (2024-01-07) es domingo.
    bars = daily(100 * 1.01 ** np.arange(60))
    sig = TsmomAgent(anchor="sunday", lookback=7).analyze(ctx_for(bars))
    assert sig.direction is Direction.LONG
    assert sig.confidence == pytest.approx(1.0)
    assert sig.metadata["r"] > 0
    assert sig.metadata["tranches_long"] == 1
    assert sig.metadata["anchor"] == "sunday"
    assert sig.metadata["lookback"] == 7


def test_downtrend_sunday_is_flat():
    bars = daily(100 * 0.99 ** np.arange(60))
    sig = TsmomAgent(anchor="sunday", lookback=7).analyze(ctx_for(bars))
    assert sig.direction is Direction.FLAT
    assert sig.confidence == 0.0
    assert sig.metadata["r"] < 0
    assert sig.metadata["tranches_long"] == 0


def test_uptrend_staggered_is_fully_long():
    bars = daily(100 * 1.01 ** np.arange(60))
    sig = TsmomAgent(anchor="staggered", lookback=7).analyze(ctx_for(bars))
    assert sig.direction is Direction.LONG
    assert sig.metadata["tranches_long"] == HOLD_DAYS
    assert sig.confidence == pytest.approx(1.0)


def test_downtrend_staggered_is_flat():
    bars = daily(100 * 0.99 ** np.arange(60))
    sig = TsmomAgent(anchor="staggered", lookback=7).analyze(ctx_for(bars))
    assert sig.direction is Direction.FLAT
    assert sig.metadata["tranches_long"] == 0
    assert sig.confidence == 0.0


# --- lógica de tramos ---------------------------------------------------------------------


def test_staggered_tranches_mix_counts_fraction():
    # Serie plana con una subida reciente: solo los tramos cuyo anclaje cae en la
    # porción alcista deben contarse en largo. Hacemos una serie plana en 100 que sube
    # a 110 los últimos tres días.
    n = 30
    closes = np.r_[np.full(n - 3, 100.0), [110.0, 110.0, 110.0]]
    bars = daily(closes)
    sig = TsmomAgent(anchor="staggered", lookback=7).analyze(ctx_for(bars))
    # Fracción debe coincidir con tranches_long / 7
    assert sig.confidence == pytest.approx(sig.metadata["tranches_long"] / HOLD_DAYS)
    assert 0 < sig.metadata["tranches_long"] <= HOLD_DAYS
    assert sig.direction is Direction.LONG


def test_tranche_long_helper_is_long_when_recent_anchor_positive():
    closes = np.r_[np.full(20, 100.0), [110.0, 111.0]]
    bars = daily(closes)
    dates = pd.DatetimeIndex(bars.index)
    # Último índice
    now_pos = len(closes) - 1
    # El día de hoy es el DOW de now_pos: para ese DOW el anclaje es "hoy", r > 0.
    today_dow = int(dates.day_of_week[now_pos])
    assert _tranche_long(closes, dates, today_dow, 7, now_pos) is True


def test_tranche_long_helper_is_flat_without_recent_anchor_up():
    # Serie plana -> ningún tramo en largo.
    closes = np.full(30, 100.0)
    bars = daily(closes)
    dates = pd.DatetimeIndex(bars.index)
    now_pos = len(closes) - 1
    for dow in range(7):
        assert _tranche_long(closes, dates, dow, 7, now_pos) is False


# --- sin look-ahead -----------------------------------------------------------------------


def test_no_lookahead_future_bars_do_not_change_signal():
    base = daily(100 * 1.01 ** np.arange(40))
    now = (base.index[-1] + pd.Timedelta(days=1)).to_pydatetime()
    a = TsmomAgent(anchor="staggered", lookback=7).analyze(ctx_for(base, now))

    # Añadir 10 velas futuras y mover la última a una inversión fuerte: la señal no cambia.
    extended_close = np.r_[base["close"].to_numpy(), 100 * 0.5 ** np.arange(1, 11)]
    extended = daily(extended_close)
    b = TsmomAgent(anchor="staggered", lookback=7).analyze(ctx_for(extended, now))
    assert a.direction is b.direction
    assert a.confidence == pytest.approx(b.confidence)
    assert a.metadata["r"] == pytest.approx(b.metadata["r"])
    assert a.metadata["tranches_long"] == b.metadata["tranches_long"]


def test_no_lookahead_ignores_unclosed_bar():
    bars = daily(100 * 1.01 ** np.arange(30))
    spike = bars.copy()
    spike.iloc[-1, spike.columns.get_loc("close")] = 1.0  # vela aún abierta
    now = bars.index[-1] + pd.Timedelta(hours=12)
    a = TsmomAgent(anchor="sunday", lookback=7).analyze(ctx_for(bars, now.to_pydatetime()))
    b = TsmomAgent(anchor="sunday", lookback=7).analyze(ctx_for(spike, now.to_pydatetime()))
    assert a == b


# --- historial insuficiente ---------------------------------------------------------------


def test_insufficient_history_is_flat():
    # Necesita lookback + HOLD_DAYS = 14 velas cerradas.
    bars = daily([100 + i for i in range(10)])
    sig = TsmomAgent(anchor="staggered", lookback=7).analyze(ctx_for(bars))
    assert sig.direction is Direction.FLAT
    assert sig.confidence == 0.0
    assert sig.metadata["r"] is None
    assert sig.metadata["tranches_long"] == 0
    assert "historial insuficiente" in sig.metadata["reason"]


def test_insufficient_history_returns_vol_when_possible():
    # Con >= 2 velas se puede calcular vol anualizada; con 1 o 0 devuelve None.
    bars = daily([100, 101, 102])
    sig = TsmomAgent(anchor="staggered", lookback=7).analyze(ctx_for(bars))
    assert sig.direction is Direction.FLAT
    assert sig.metadata["annualized_vol"] is not None


def test_empty_bars_returns_flat():
    empty = daily([]).iloc[:0]
    sig = TsmomAgent(anchor="staggered").analyze(ctx_for(empty, datetime(2024, 1, 2, tzinfo=UTC)))
    assert sig.direction is Direction.FLAT
    assert sig.metadata["annualized_vol"] is None


# --- metadata y vol ------------------------------------------------------------------------


def test_metadata_contains_required_fields():
    bars = daily(100 * 1.01 ** np.arange(60))
    sig = TsmomAgent(anchor="staggered", lookback=7).analyze(ctx_for(bars))
    for key in ("r", "tranches_long", "annualized_vol", "anchor", "lookback", "bars_used"):
        assert key in sig.metadata
    assert sig.metadata["annualized_vol"] is not None


def test_annualized_vol_helper_matches_expected():
    rng = np.random.default_rng(42)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 400)))
    vol = _annualized_vol(closes, window=60)
    expected = np.std(np.diff(np.log(closes[-61:])), ddof=1) * np.sqrt(365)
    assert vol == pytest.approx(expected)


def test_annualized_vol_none_for_short_series():
    assert _annualized_vol(np.array([100.0])) is None
    assert _annualized_vol(np.empty(0)) is None


# --- validación de parámetros --------------------------------------------------------------


@pytest.mark.parametrize("bad", [3, 5, 8, 21, 60, 0])
def test_invalid_lookbacks(bad):
    with pytest.raises(ValueError, match="lookback"):
        TsmomAgent(lookback=bad)


@pytest.mark.parametrize("bad", ["monday", "", "STAGGERED", "weekly"])
def test_invalid_anchors(bad):
    with pytest.raises(ValueError, match="anchor"):
        TsmomAgent(anchor=bad)


@pytest.mark.parametrize("lookback", [7, 14, 28])
def test_allowed_lookbacks(lookback):
    agent = TsmomAgent(lookback=lookback)
    assert agent.lookback == lookback


# --- helpers y timestamp -------------------------------------------------------------------


def test_last_sunday_index_returns_latest():
    # 2024-01-01 es lunes. Domingos caen en posiciones 6 (2024-01-07) y 13 (2024-01-14).
    idx = pd.date_range("2024-01-01", periods=15, freq="D", tz="UTC")
    assert _last_sunday_index(idx, min_pos=0) == 13
    assert _last_sunday_index(idx, min_pos=7) == 13
    assert _last_sunday_index(idx, min_pos=14) is None


def test_signal_timestamp_is_now():
    bars = daily(100 * 1.01 ** np.arange(30))
    now = datetime(2024, 2, 1, tzinfo=UTC)
    sig = TsmomAgent(lookback=7).analyze(ctx_for(bars, now))
    assert sig.generated_at == now


def test_registered_and_buildable():
    agents = build_strategies({"tsmom": {"lookback": 14, "anchor": "sunday"}})
    assert isinstance(agents[0], TsmomAgent)
    assert agents[0].lookback == 14
    assert agents[0].anchor == "sunday"


def test_naive_bar_index_is_handled():
    bars = daily(100 * 1.01 ** np.arange(60))
    bars.index = bars.index.tz_localize(None)
    now = (bars.index[-1] + pd.Timedelta(days=1)).tz_localize("UTC").to_pydatetime()
    sig = TsmomAgent(anchor="sunday", lookback=7).analyze(
        AgentContext(symbol="BTCUSDT", now=now, bars={Timeframe.D1: bars})
    )
    assert sig.direction is Direction.LONG
