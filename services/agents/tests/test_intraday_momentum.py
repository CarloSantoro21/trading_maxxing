from datetime import datetime

import numpy as np
import pandas as pd
import pytest
from tm_agents.base import AgentContext, build_strategies, registered_strategies
from tm_agents.strategies import IntradayMomentumAgent
from tm_agents.strategies.intraday_momentum import (
    MARKET_TZ,
    FeeCheck,
    fee_check,
    parse_slot,
    rule_is_long,
    session_table,
    slot_label,
    timing_position,
    volume_clock_open,
    volume_profile,
)
from tm_shared.contracts import Direction, Horizon, Timeframe

ET = MARKET_TZ


def et(day: str, hhmm: str) -> pd.Timestamp:
    return pd.Timestamp(f"{day} {hhmm}", tz=ET)


def make_bars(
    start: str,
    days: int,
    moves: dict[pd.Timestamp, float] | None = None,
    spike_et: str | None = "09:30",
) -> pd.DataFrame:
    """Velas de 30 min (índice UTC) con precio plano salvo los `moves` dados.

    `moves` mapea el instante ET de apertura de una vela a su retorno simple. El volumen es 1
    y, si `spike_et` no es None, salta a 3 desde esa hora ET hasta las 17:00 ET.
    """
    first = pd.Timestamp(start, tz="UTC")
    idx = pd.date_range(first, periods=days * 48, freq="30min", name="open_time")
    et_idx = idx.tz_convert(ET)
    rets = np.zeros(len(idx))
    for ts, r in (moves or {}).items():
        pos = idx.get_loc(ts.tz_convert("UTC"))
        rets[pos] = r
    close = 100.0 * np.cumprod(1.0 + rets)
    open_ = np.r_[100.0, close[:-1]]
    slot = et_idx.hour * 2 + et_idx.minute // 30
    volume = np.ones(len(idx))
    if spike_et is not None:
        volume[(slot >= parse_slot(spike_et)) & (slot < 34)] = 3.0
    return pd.DataFrame(
        {
            "open": open_,
            "high": np.maximum(open_, close),
            "low": np.minimum(open_, close),
            "close": close,
            "volume": volume,
        },
        index=idx,
    )


def ctx(bars: pd.DataFrame, now: pd.Timestamp) -> AgentContext:
    return AgentContext(
        symbol="BTCUSDT",
        now=now.tz_convert("UTC").to_pydatetime(),
        bars={Timeframe.M30: bars},
    )


DAY = "2024-03-20"  # horario de verano en EE. UU.: ET = UTC-4


def day_moves(onfh_overnight=0.0, first=0.0, slh=0.0, last=0.0, day=DAY, open_="09:30"):
    """Movimientos de un día: noche (21:00 ET del día previo), primera media hora, 16:00 y 16:30."""
    prev = (pd.Timestamp(day) - pd.Timedelta(days=1)).date().isoformat()
    return {
        et(prev, "21:00"): onfh_overnight,
        et(day, open_): first,
        et(day, "16:00"): slh,
        et(day, "16:30"): last,
    }


# --- utilidades ---------------------------------------------------------------------------


def test_parse_slot_and_label():
    assert parse_slot("00:00") == 0
    assert parse_slot("09:30") == 19
    assert parse_slot(47) == 47
    assert slot_label(19) == "09:30" and slot_label(34) == "17:00"
    for bad in ("9", "09:15", "24:00", "x:y"):
        with pytest.raises(ValueError):
            parse_slot(bad)
    with pytest.raises(ValueError):
        parse_slot(48)


def test_volume_clock_finds_the_spike():
    bars = make_bars("2024-03-01", 20, spike_et="09:30")
    profile = volume_profile(bars)
    assert profile.idxmax() >= 19
    assert volume_clock_open(bars) == 19


def test_volume_clock_respects_window_and_default():
    bars = make_bars("2024-04-01", 20, spike_et="09:30")  # sin cambio de horario en medio
    # El salto de las 9:30 queda fuera de la ventana, así que no puede ganar.
    assert 0 <= volume_clock_open(bars, window=("00:00", "08:00")) <= 16
    assert volume_clock_open(bars, window=("09:30", "09:30")) == 19
    empty = bars.iloc[:0]
    assert volume_clock_open(empty, default=18) == 18
    with pytest.raises(ValueError):
        volume_clock_open(bars, window=("10:00", "16:00"))


def test_volume_clock_uses_market_time_across_dst():
    # En invierno ET = UTC-5: la apertura sigue en 9:30 ET aunque en UTC se mueva una hora.
    winter = make_bars("2024-01-05", 20, spike_et="09:30")
    assert volume_clock_open(winter) == 19


# --- retornos del paper -------------------------------------------------------------------


def test_session_table_matches_paper_definitions():
    moves = day_moves(onfh_overnight=0.01, first=0.02, slh=-0.005, last=0.003)
    bars = make_bars("2024-03-15", 8, moves)
    row = session_table(bars, open_slot=19).loc[pd.Timestamp(DAY)]
    assert row["r_onfh"] == pytest.approx(1.01 * 1.02 - 1)
    assert row["r_slh"] == pytest.approx(-0.005)
    assert row["r_lh"] == pytest.approx(0.003)
    assert row["first_volume"] == pytest.approx(3.0)


def test_session_table_nan_when_bars_missing():
    bars = make_bars("2024-03-15", 8, day_moves(first=0.02))
    drop = et(DAY, "16:00").tz_convert("UTC")
    row = session_table(bars.drop(index=drop), open_slot=19).loc[pd.Timestamp(DAY)]
    assert np.isnan(row["r_slh"]) and np.isfinite(row["r_onfh"])
    with pytest.raises(ValueError):
        session_table(bars, open_slot=32)


def test_naive_index_is_treated_as_utc():
    bars = make_bars("2024-03-15", 8, day_moves(first=0.02))
    naive = bars.tz_localize(None)
    a = session_table(bars, 19)
    b = session_table(naive, 19)
    pd.testing.assert_frame_equal(a, b)


@pytest.mark.parametrize(
    ("onfh", "slh", "mode", "expected"),
    [
        (0.01, 0.0, "onfh", 1),
        (0.0, 0.0, "onfh", -1),
        (-0.01, 0.0, "onfh", -1),
        (np.nan, 0.0, "onfh", 0),
        (0.0, -0.01, "slh", 1),
        (0.0, 0.0, "slh", -1),
        (0.0, np.nan, "slh", 0),
        (0.01, -0.01, "both", 1),
        (-0.01, 0.01, "both", -1),
        (0.0, 0.0, "both", -1),
        (0.01, 0.01, "both", 0),
        (-0.01, -0.01, "both", 0),
        (0.01, np.nan, "both", 0),
    ],
)
def test_timing_position_follows_paper_equations(onfh, slh, mode, expected):
    assert timing_position(onfh, slh, mode) == expected


def test_rule_is_long_is_spot_and_threshold():
    assert rule_is_long(0.01, 0.0, "onfh")
    assert not rule_is_long(-0.01, 0.0, "onfh")  # el corto del paper es flat en spot
    assert not rule_is_long(0.004, 0.0, "onfh", threshold=0.005)
    assert rule_is_long(np.nan, -0.01, "slh", threshold=0.5)  # slh ignora el umbral de onfh
    with pytest.raises(ValueError):
        timing_position(0.0, 0.0, "nope")


# --- agente -------------------------------------------------------------------------------


def test_registered_and_buildable():
    assert registered_strategies()["intraday_momentum"] is IntradayMomentumAgent
    (agent,) = build_strategies({"intraday_momentum": {"mode": "both", "open_time": "09:00"}})
    assert isinstance(agent, IntradayMomentumAgent)
    assert agent.mode == "both" and agent.fixed_open == 18
    assert agent.timeframe is Timeframe.M30


def test_long_in_last_half_hour_after_positive_first_half_hour():
    bars = make_bars("2024-03-01", 25, day_moves(first=0.02))
    sig = IntradayMomentumAgent().analyze(ctx(bars, et(DAY, "16:30")))
    assert sig.direction is Direction.LONG
    assert sig.horizon is Horizon.SHORT and sig.source == "intraday_momentum"
    assert 0 < sig.confidence <= 1
    md = sig.metadata
    assert md["open_time_et"] == "09:30" and md["open_source"] == "volume"
    assert md["r_onfh"] == pytest.approx(0.02)
    assert md["paper_position"] == 1
    # 17:00 ET en verano = 21:00 UTC
    assert md["exit_at"] == "2024-03-20T21:00:00+00:00"


def test_dst_winter_exit_time():
    day = "2024-01-24"
    bars = make_bars("2024-01-01", 25, day_moves(first=0.02, day=day))
    sig = IntradayMomentumAgent().analyze(ctx(bars, et(day, "16:45")))
    assert sig.direction is Direction.LONG
    assert sig.metadata["exit_at"] == "2024-01-24T22:00:00+00:00"


def test_flat_outside_last_half_hour():
    bars = make_bars("2024-03-01", 25, day_moves(first=0.02))
    for hhmm in ("10:30", "16:00", "16:29"):
        sig = IntradayMomentumAgent().analyze(ctx(bars, et(DAY, hhmm)))
        assert sig.direction is Direction.FLAT and sig.confidence == 0.0
        assert "fuera" in sig.metadata["reason"]
        assert sig.metadata["paper_position"] == 0
    sig = IntradayMomentumAgent().analyze(ctx(bars, et(DAY, "17:00")))
    assert sig.direction is Direction.FLAT


def test_negative_first_half_hour_is_flat_not_short():
    bars = make_bars("2024-03-01", 25, day_moves(first=-0.02))
    sig = IntradayMomentumAgent().analyze(ctx(bars, et(DAY, "16:30")))
    assert sig.direction is Direction.FLAT
    assert sig.metadata["paper_position"] == -1
    assert "corto" in sig.metadata["reason"]


def test_overnight_counts_in_first_return():
    # Primera media hora plana, pero la noche subió: r_onfh > 0 (definición del paper).
    bars = make_bars("2024-03-01", 25, day_moves(onfh_overnight=0.01))
    sig = IntradayMomentumAgent().analyze(ctx(bars, et(DAY, "16:30")))
    assert sig.direction is Direction.LONG
    assert sig.metadata["r_onfh"] == pytest.approx(0.01)


def test_both_mode_needs_negative_second_to_last():
    up_up = make_bars("2024-03-01", 25, day_moves(first=0.02, slh=0.01))
    up_down = make_bars("2024-03-01", 25, day_moves(first=0.02, slh=-0.01))
    agent = IntradayMomentumAgent(mode="both")
    assert agent.analyze(ctx(up_up, et(DAY, "16:30"))).direction is Direction.FLAT
    assert agent.analyze(ctx(up_down, et(DAY, "16:30"))).direction is Direction.LONG


def test_slh_mode_reversal():
    bars = make_bars("2024-03-01", 25, day_moves(first=-0.02, slh=-0.01))
    sig = IntradayMomentumAgent(mode="slh").analyze(ctx(bars, et(DAY, "16:30")))
    assert sig.direction is Direction.LONG
    assert sig.metadata["r_slh"] == pytest.approx(-0.01)


def test_threshold_filters_small_moves():
    bars = make_bars("2024-03-01", 25, day_moves(first=0.002))
    now = et(DAY, "16:30")
    assert IntradayMomentumAgent().analyze(ctx(bars, now)).direction is Direction.LONG
    sig = IntradayMomentumAgent(threshold=0.005).analyze(ctx(bars, now))
    assert sig.direction is Direction.FLAT and sig.metadata["reason"] == "la regla no se cumple"


def test_no_look_ahead():
    base = day_moves(first=0.02)
    future = dict(base)
    future[et(DAY, "16:30")] = -0.5  # la vela que se opera todavía no cierra
    future[et("2024-03-21", "09:30")] = 0.3
    now = et(DAY, "16:30")
    a = IntradayMomentumAgent().analyze(ctx(make_bars("2024-03-01", 25, base), now))
    b = IntradayMomentumAgent().analyze(ctx(make_bars("2024-03-01", 25, future), now))
    assert a.direction is b.direction is Direction.LONG
    assert a.confidence == b.confidence
    assert a.metadata == b.metadata


def test_confidence_scales_with_sigma():
    moves = {}
    rng = np.random.default_rng(0)
    for d in pd.date_range("2024-03-02", "2024-03-19", freq="D"):
        moves[et(d.date().isoformat(), "09:30")] = float(rng.normal(0, 0.01))
    small = dict(moves) | day_moves(first=0.001)
    big = dict(moves) | day_moves(first=0.2)
    now = et(DAY, "16:30")
    s = IntradayMomentumAgent().analyze(ctx(make_bars("2024-03-01", 25, small), now))
    b = IntradayMomentumAgent().analyze(ctx(make_bars("2024-03-01", 25, big), now))
    assert s.metadata["onfh_sigma"] is not None
    assert 0 < s.confidence < b.confidence == 1.0


def test_default_open_with_little_history_and_fixed_open():
    bars = make_bars("2024-03-18", 3, day_moves(first=0.02, open_="09:00"))
    now = et(DAY, "16:30")
    sig = IntradayMomentumAgent().analyze(ctx(bars, now))
    assert sig.metadata["open_source"] == "default" and sig.metadata["open_time_et"] == "09:00"
    assert sig.direction is Direction.LONG
    assert sig.confidence == 1.0  # sin historia suficiente para sigma
    fixed = IntradayMomentumAgent(open_time="09:00").analyze(ctx(bars, now))
    assert fixed.metadata["open_source"] == "fixed"


def test_missing_bars_is_flat():
    bars = make_bars("2024-03-01", 25, day_moves(first=0.02))
    bars = bars.drop(index=et(DAY, "16:00").tz_convert("UTC"))
    sig = IntradayMomentumAgent(mode="both").analyze(ctx(bars, et(DAY, "16:30")))
    assert sig.direction is Direction.FLAT
    assert sig.metadata["reason"] == "faltan velas para calcular la señal"


def test_empty_bars():
    bars = make_bars("2024-03-01", 1).iloc[:0]
    sig = IntradayMomentumAgent().analyze(ctx(bars, et(DAY, "16:30")))
    assert sig.direction is Direction.FLAT
    assert sig.metadata["bars_used"] == 0 and sig.metadata["last_close"] is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"mode": "x"},
        {"volume_lookback_days": 0},
        {"open_time": "16:00"},
        {"open_time": "9"},
        {"default_open": 40},
    ],
)
def test_invalid_params(kwargs):
    with pytest.raises(ValueError):
        IntradayMomentumAgent(**kwargs)


def test_generated_at_is_now():
    bars = make_bars("2024-03-01", 25, day_moves(first=0.02))
    now = et(DAY, "16:30")
    sig = IntradayMomentumAgent().analyze(ctx(bars, now))
    assert sig.generated_at == now.tz_convert("UTC").to_pydatetime()
    assert isinstance(sig.generated_at, datetime)


# --- chequeo de comisiones ----------------------------------------------------------------


def sessions_frame(rows):
    idx = pd.date_range("2024-01-01", periods=len(rows), freq="D", name="session_date")
    return pd.DataFrame(rows, columns=["r_onfh", "r_slh", "r_lh"], index=idx)


def test_fee_check_known_result():
    s = sessions_frame(
        [
            (0.01, 0.0, 0.004),  # largo, gana 40 bp
            (0.02, 0.0, -0.002),  # largo, pierde 20 bp
            (-0.01, 0.0, 0.01),  # corto en el paper: no se opera en spot
            (0.01, 0.0, np.nan),  # día incompleto: se ignora
        ]
    )
    r = fee_check(s, fee_per_side=0.001)
    assert isinstance(r, FeeCheck)
    assert r.days == 3 and r.trades == 2
    assert r.fee_per_trade == pytest.approx(0.002)
    assert r.mean_gross_per_trade == pytest.approx(0.001)
    assert r.mean_net_per_trade == pytest.approx(-0.001)
    assert r.breakeven_fee_per_side == pytest.approx(0.0005)
    assert r.hit_rate == pytest.approx(0.5)
    assert r.years == pytest.approx(3 / 365.25)
    assert r.trades_per_year == pytest.approx(2 / r.years)
    assert r.net_annual == pytest.approx(r.gross_annual - r.fees_annual)


def test_fee_check_recovers_regression():
    rng = np.random.default_rng(1)
    onfh = rng.normal(0, 0.03, 500)
    slh = rng.normal(0, 0.008, 500)
    lh = 0.0001 + 0.1 * onfh - 0.3 * slh
    r = fee_check(sessions_frame(list(zip(onfh, slh, lh, strict=True))), mode="both")
    assert r.beta_onfh == pytest.approx(0.1)
    assert r.beta_slh == pytest.approx(-0.3)
    assert r.r2 == pytest.approx(1.0)


def test_fee_check_edge_cases():
    with pytest.raises(ValueError):
        fee_check(sessions_frame([(np.nan, 0.0, 0.0)]))
    r = fee_check(sessions_frame([(-0.01, 0.0, 0.0), (-0.02, 0.0, 0.0)]))
    assert r.trades == 0 and r.hit_rate is None and r.mean_net_per_trade is None
    assert r.beta_onfh is None and r.breakeven_fee_per_side is None
