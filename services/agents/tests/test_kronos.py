"""KronosAgent con un servidor forecast simulado (respx): sin red ni pesos de Kronos."""

import json

import httpx
import numpy as np
import pandas as pd
import pytest
import respx
from tm_agents.base import AgentContext, build_strategies, registered_strategies
from tm_agents.strategies import KronosAgent
from tm_agents.strategies.kronos import (
    ForecastClient,
    ForecastError,
    bars_4h,
    candles_payload,
    hit_probabilities,
    parse_predict_response,
)
from tm_shared.contracts import Direction, Horizon, Timeframe

URL = "http://forecast.test"


def bars_15m(n_4h=100, start="2025-01-01", last=100.0):
    """`n_4h` velas de 4 h completas en 15m, con cierre final `last`."""
    n = n_4h * 16
    idx = pd.date_range(start, periods=n, freq="15min", tz="UTC", name="open_time")
    close = np.linspace(last * 0.9, last, n)
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.001,
            "low": close * 0.999,
            "close": close,
            "volume": 1.0,
        },
        index=idx,
    )


M15 = pd.Timedelta(minutes=15)


def ctx_for(bars, tf=Timeframe.M15, now=None, period=M15):
    now = now or (bars.index[-1] + period).to_pydatetime()
    return AgentContext(symbol="BTCUSDT", now=now, bars={tf: bars})


def response(final_returns, last=100.0, pred_len=6, high_mult=1.001, low_mult=0.999):
    """Respuesta de /predict: cada trayectoria va lineal de `last` a `last*(1+r)`."""
    stamps = pd.date_range("2026-01-01", periods=pred_len, freq="4h", tz="UTC")
    trajectories = []
    for r in final_returns:
        closes = np.linspace(last, last * (1 + r), pred_len + 1)[1:]
        trajectories.append(
            [
                {
                    "timestamp": ts.isoformat(),
                    "open": c,
                    "high": c * high_mult,
                    "low": c * low_mult,
                    "close": c,
                    "volume": 1.0,
                }
                for ts, c in zip(stamps, closes, strict=True)
            ]
        )
    return {
        "model": "fake",
        "device": "cpu",
        "pred_len": pred_len,
        "samples": len(final_returns),
        "seed": 42,
        "timestamps": [ts.isoformat() for ts in stamps],
        "trajectories": trajectories,
    }


def agent(**kw):
    return KronosAgent(client=ForecastClient(URL, timeout=1.0), **kw)


@respx.mock
def test_long_when_median_positive_and_confidence_is_agreement():
    rets = [0.03] * 15 + [-0.01] * 5
    route = respx.post(f"{URL}/predict").mock(return_value=httpx.Response(200, json=response(rets)))
    sig = agent().analyze(ctx_for(bars_15m()))
    assert sig.direction is Direction.LONG
    assert sig.confidence == pytest.approx(0.75)
    assert sig.timeframe is Timeframe.H4 and sig.horizon is Horizon.SHORT
    assert sig.source == "kronos"
    md = sig.metadata
    assert md["n_trajectories"] == 20 and md["pred_len"] == 6
    assert md["median_return"] == pytest.approx(0.03)
    assert md["p_up"] == pytest.approx(0.75)
    assert md["model"] == "fake" and "error" not in md
    body = json.loads(route.calls.last.request.content)
    assert body["samples"] == 20 and body["pred_len"] == 6 and body["seed"] == 42
    assert len(body["candles"]) == 100  # 100 velas de 4 h remuestreadas de 15m
    assert body["candles"][-1]["close"] == pytest.approx(100.0)


@respx.mock
def test_flat_in_spot_when_median_negative():
    rets = [-0.02] * 7 + [0.01] * 3
    respx.post(f"{URL}/predict").mock(return_value=httpx.Response(200, json=response(rets)))
    sig = agent(n_trajectories=10).analyze(ctx_for(bars_15m()))
    assert sig.direction is Direction.FLAT
    assert sig.confidence == pytest.approx(0.7)
    assert sig.score == 0.0


@respx.mock
def test_short_only_when_allowed():
    rets = [-0.02] * 8 + [0.01] * 2
    respx.post(f"{URL}/predict").mock(return_value=httpx.Response(200, json=response(rets)))
    sig = agent(n_trajectories=10, allow_short=True).analyze(ctx_for(bars_15m()))
    assert sig.direction is Direction.SHORT and sig.confidence == pytest.approx(0.8)


@respx.mock
def test_min_return_deadband_gives_flat():
    rets = [0.005] * 10
    respx.post(f"{URL}/predict").mock(return_value=httpx.Response(200, json=response(rets)))
    sig = agent(n_trajectories=10, min_return=0.01).analyze(ctx_for(bars_15m()))
    assert sig.direction is Direction.FLAT and sig.confidence == 0.0


@respx.mock
def test_n_trajectories_configurable_and_trajectories_cached():
    rets = [0.01, 0.02, -0.01, 0.03, 0.0]
    route = respx.post(f"{URL}/predict").mock(
        return_value=httpx.Response(200, json=response(rets, pred_len=3))
    )
    a = agent(n_trajectories=5, pred_len=3, context=64)
    ctx = ctx_for(bars_15m())
    paths = a.trajectories(ctx)
    assert paths.shape == (5, 3, 5)
    assert paths[1, -1, 3] == pytest.approx(102.0)
    a.analyze(ctx)
    assert route.call_count == 1  # analyze reutiliza el pronóstico del mismo ciclo
    body = json.loads(route.calls.last.request.content)
    assert body["samples"] == 5 and len(body["candles"]) == 64


@respx.mock
def test_timeout_gives_flat_with_error():
    respx.post(f"{URL}/predict").mock(side_effect=httpx.ReadTimeout("lento"))
    sig = agent().analyze(ctx_for(bars_15m()))
    assert sig.direction is Direction.FLAT and sig.confidence == 0.0
    assert "timeout" in sig.metadata["error"]


@respx.mock
def test_connection_error_gives_flat_with_error():
    respx.post(f"{URL}/predict").mock(side_effect=httpx.ConnectError("caído"))
    sig = agent().analyze(ctx_for(bars_15m()))
    assert sig.confidence == 0.0 and "no responde" in sig.metadata["error"]


@respx.mock
def test_http_error_status_gives_flat_with_error():
    respx.post(f"{URL}/predict").mock(return_value=httpx.Response(503, text="cargando"))
    sig = agent().analyze(ctx_for(bars_15m()))
    assert sig.direction is Direction.FLAT and "503" in sig.metadata["error"]


@respx.mock
def test_malformed_response_gives_flat_with_error():
    bad = response([0.01] * 3)  # se piden 20, llegan 3
    respx.post(f"{URL}/predict").mock(return_value=httpx.Response(200, json=bad))
    sig = agent().analyze(ctx_for(bars_15m()))
    assert "inválida" in sig.metadata["error"]
    respx.post(f"{URL}/predict").mock(return_value=httpx.Response(200, text="no es json"))
    sig = agent(n_trajectories=3).analyze(ctx_for(bars_15m()))
    assert "inválida" in sig.metadata["error"]


def test_not_enough_bars_gives_flat_without_calling_forecast():
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(f"{URL}/predict")
        sig = agent().analyze(ctx_for(bars_15m(n_4h=10)))
        assert route.call_count == 0
    assert sig.direction is Direction.FLAT and "insuficientes" in sig.metadata["error"]
    with pytest.raises(ForecastError):
        agent().trajectories(ctx_for(bars_15m(n_4h=10)))


def test_hit_probabilities():
    rets = [0.06, 0.06, -0.04, 0.0]
    fc = parse_predict_response(response(rets), samples=4, pred_len=6)
    p_tp, p_sl, p_open = hit_probabilities(fc.paths, stop_loss=98.0, take_profit=104.0)
    assert (p_tp, p_sl, p_open) == (0.5, 0.25, 0.25)
    # SL y TP en la misma vela: cuenta el SL
    wide = parse_predict_response(
        response([0.0], high_mult=1.1, low_mult=0.9), samples=1, pred_len=6
    )
    assert hit_probabilities(wide.paths, 98.0, 104.0) == (0.0, 1.0, 0.0)
    with pytest.raises(ValueError):
        hit_probabilities(fc.paths, 105.0, 104.0)


@respx.mock
def test_signal_metadata_has_hit_probabilities():
    rets = [0.06] * 10
    respx.post(f"{URL}/predict").mock(return_value=httpx.Response(200, json=response(rets)))
    md = agent(n_trajectories=10).analyze(ctx_for(bars_15m())).metadata
    assert md["p_take_profit"] == 1.0 and md["p_stop_loss"] == 0.0
    assert md["take_profit"] == pytest.approx(104.0)
    assert md["forecast_end"].startswith("2026-01-01T20:00")


def test_bars_4h_sources_and_no_lookahead():
    src = bars_15m(n_4h=40)
    # `now` a mitad de la vela 40: solo hay 39 completas
    now = (src.index[-1] - pd.Timedelta(hours=2)).to_pydatetime()
    assert len(bars_4h(ctx_for(src, now=now))) == 39
    h4 = bars_4h(ctx_for(src))
    assert len(h4) == 40
    direct = bars_4h(ctx_for(h4, tf=Timeframe.H4, period=pd.Timedelta(hours=4)))
    pd.testing.assert_frame_equal(direct, h4)
    one_min = bars_15m(n_4h=2).resample("1min").ffill()
    assert len(bars_4h(ctx_for(one_min, tf=Timeframe.M1, now=src.index[0] + pd.Timedelta(hours=8))))
    with pytest.raises(KeyError):
        bars_4h(ctx_for(src, tf=Timeframe.D1))


def test_candles_payload_naive_index():
    df = bars_15m(n_4h=1).iloc[:2]
    df.index = df.index.tz_localize(None)
    payload = candles_payload(df)
    assert payload[0]["timestamp"] == "2025-01-01T00:00:00+00:00"
    assert set(payload[0]) == {"timestamp", "open", "high", "low", "close", "volume"}


def test_client_from_env_and_registry():
    c = ForecastClient.from_env({"FORECAST_URL": "http://x:9/", "FORECAST_TIMEOUT": "5"})
    assert c.base_url == "http://x:9" and c.timeout == 5.0
    d = ForecastClient.from_env({})
    assert d.base_url == "http://localhost:8001" and d.timeout == 120.0
    assert registered_strategies()["kronos"] is KronosAgent
    [a] = build_strategies({"kronos": {"enabled": True, "n_trajectories": 7}})
    assert isinstance(a, KronosAgent) and a.n_trajectories == 7


@pytest.mark.parametrize(
    "kw",
    [
        {"n_trajectories": 0},
        {"pred_len": 0},
        {"context": 10},
        {"context": 1000},
        {"stop_loss_pct": 0},
        {"take_profit_pct": 0},
    ],
)
def test_invalid_params(kw):
    with pytest.raises(ValueError):
        agent(**kw)


def test_parse_rejects_non_finite():
    data = response([0.01])
    data["trajectories"][0][0]["close"] = float("nan")
    with pytest.raises(ValueError, match="finitos"):
        parse_predict_response(data, samples=1, pred_len=6)
    with pytest.raises(ValueError, match="forma"):
        parse_predict_response(response([0.01], pred_len=3), samples=1, pred_len=6)


@respx.mock
def test_trajectories_feed_simulation_gate():
    from tm_agents.simulation_gate import PathSet

    rets = [0.02, -0.01, 0.03, 0.01]
    respx.post(f"{URL}/predict").mock(return_value=httpx.Response(200, json=response(rets)))
    paths = PathSet.from_array(agent(n_trajectories=4).trajectories(ctx_for(bars_15m())))
    assert paths.n_paths == 4 and paths.steps == 6
    assert paths.close[2, -1] == pytest.approx(103.0)
