"""Laboratorio de Kronos (#13): umbrales en jul-dic 2024, OOS 2025 y holdout 2026 con bandera."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from tm_engine.lab import HoldoutError
from tm_engine.lab.cli import main
from tm_engine.lab.kronos import (
    H4,
    KronosLabConfig,
    dir_forecast_loader,
    forecast_files,
    kronos_lab,
    kronos_targets,
    median_returns,
)

STEPS = 3
SAMPLES = 5


def _bars_4h(start="2024-06-30", end="2026-11-01", seed=0):
    idx = pd.date_range(start, end, freq="4h", tz="UTC", inclusive="left", name="open_time")
    rng = np.random.default_rng(seed)
    close = np.round(100.0 * np.cumprod(1.0 + rng.normal(0.0, 0.01, len(idx))), 2)
    opens = np.r_[close[0], close[:-1]]
    return pd.DataFrame(
        {
            "open": opens,
            "high": np.maximum(opens, close),
            "low": np.minimum(opens, close),
            "close": close,
            "volume": 1.0,
        },
        index=idx,
    )


def _forecasts(bars: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    """Pronósticos "perfectos": la mediana es el retorno real de la vela siguiente."""
    closes = bars["close"]
    times = pd.DatetimeIndex(bars.index) + H4  # instante de decisión = cierre de la vela
    rows = []
    for i, t in enumerate(times[:-1]):
        if not pd.Timestamp(start, tz="UTC") <= t < pd.Timestamp(end, tz="UTC"):
            continue
        last, nxt = float(closes.iloc[i]), float(closes.iloc[i + 1])
        for s in range(SAMPLES):
            for step in range(STEPS):
                px = nxt * (1 + 0.001 * (s - SAMPLES // 2)) if step == STEPS - 1 else last
                rows.append((t, s, step, t + step * H4, px, px, px, px, 1.0, last, 400))
    cols = ["decision_time", "sample", "step", "timestamp", "open", "high", "low", "close"]
    return pd.DataFrame(rows, columns=[*cols, "volume", "last_close", "context_len"])


def _write_forecasts(root, symbol, fc: pd.DataFrame) -> None:
    folder = root / symbol / "4h"
    folder.mkdir(parents=True)
    for day, part in fc.groupby(pd.DatetimeIndex(fc["decision_time"]).normalize()):
        part.to_parquet(folder / f"{day.date()}.parquet")
    (folder / "notas.parquet").write_bytes(b"")  # nombres que no son fecha se ignoran


@pytest.fixture
def kronos_env(tmp_path):
    bars = _bars_4h()
    fc = _forecasts(bars, "2024-07-01", "2026-11-01")
    _write_forecasts(tmp_path, "BTCUSDT", fc)
    opened: list[tuple[pd.Timestamp, pd.Timestamp]] = []

    def bar_loader(symbol, start, end):
        opened.append((start, end))
        return bars

    return {"root": tmp_path, "bars": bars, "bar_loader": bar_loader, "opened": opened}


def test_median_returns_and_targets():
    t0 = pd.Timestamp("2024-07-01 04:00", tz="UTC")
    fc = pd.DataFrame(
        {
            "decision_time": [t0] * 6,
            "sample": [0, 0, 1, 1, 2, 2],
            "step": [0, 1] * 3,
            "close": [1.0, 102.0, 1.0, 101.0, 1.0, 90.0],
            "last_close": [100.0] * 6,
        }
    )
    med = median_returns(fc)
    assert med.loc[t0] == pytest.approx(0.01)  # mediana de +2 %, +1 % y -10 %
    bars = _bars_4h("2024-07-01", "2024-07-02")
    target = kronos_targets(bars, med, 0.005)
    assert target.loc[t0 - H4] == 1.0  # la decisión al cierre de la vela que abre a las 00:00
    assert target.sum() == 1.0
    assert kronos_targets(bars, med, 0.02).sum() == 0.0


def test_kronos_lab_picks_threshold_without_touching_2026(kronos_env):
    cfg = KronosLabConfig(
        symbols=("BTCUSDT",), forecasts_dir=str(kronos_env["root"]), n_simulations=200,
        thresholds=(0.0, 0.5),
    )  # fmt: skip
    result = kronos_lab(cfg, bar_loader=kronos_env["bar_loader"])
    assert result["holdout"] is None
    assert all(end <= pd.Timestamp("2026-01-01", tz="UTC") for _, end in kronos_env["opened"])
    cov = result["coverage"]["BTCUSDT"]
    assert cov["last"] < "2026-01-01"
    # 2025 es out-of-sample: se evalúa con el umbral elegido, Monte Carlo a 1 año y criterios
    oos = result["out_of_sample"]
    assert oos["strategy"]["start"] == "2025-01-01" and oos["strategy"]["end"] == "2026-01-01"
    assert oos["trades"] > 0 and oos["montecarlo"]["horizon_days"] == 365
    assert {"approved", "reasons"} <= set(oos["approval"])
    # pronóstico perfecto: el umbral 0 gana; con 0.5 nunca entra
    assert result["chosen"]["min_return"] == 0.0
    assert result["grid"]["0.5000"]["trades"] == 0
    ins = result["in_sample"]
    assert ins["strategy"]["total_return"] > ins["buy_and_hold"]["total_return"]
    assert ins["trades"] > 0 and ins["commissions"] > 0
    assert ins["equity_daily"][0] == ["2024-07-01", 100000.0]


def test_kronos_files_of_2026_are_not_opened_without_flag(kronos_env):
    root = kronos_env["root"]
    start, end = pd.Timestamp("2024-07-01", tz="UTC"), pd.Timestamp("2026-01-01", tz="UTC")
    files = forecast_files(root, "BTCUSDT", start, end)
    assert files and all(f.stem < "2026-01-01" for f in files)
    loaded = dir_forecast_loader(root)("BTCUSDT", start, end)
    assert pd.DatetimeIndex(loaded["decision_time"]).max() < end
    with pytest.raises(FileNotFoundError):
        dir_forecast_loader(root)("ETHUSDT", start, end)
    with pytest.raises(HoldoutError):
        kronos_lab(KronosLabConfig(is_end="2026-03-01", forecasts_dir=str(root)))
    with pytest.raises(HoldoutError):
        kronos_lab(KronosLabConfig(oos_end="2026-03-01", forecasts_dir=str(root)))
    with pytest.raises(ValueError, match="después"):
        kronos_lab(KronosLabConfig(oos_start="2024-12-01", forecasts_dir=str(root)))


def test_kronos_holdout_runs_only_with_flag(kronos_env):
    cfg = KronosLabConfig(
        symbols=("BTCUSDT",), forecasts_dir=str(kronos_env["root"]), include_holdout=True,
        n_simulations=200, thresholds=(0.0,), engine="nautilus",
    )  # fmt: skip
    result = kronos_lab(cfg, bar_loader=kronos_env["bar_loader"])
    hold = result["holdout"]
    assert hold["strategy"]["start"] == "2026-01-01" and hold["strategy"]["end"] == "2026-11-01"
    assert hold["trades"] > 0
    assert {"approved", "reasons"} <= set(hold["approval"])
    assert hold["montecarlo"]["max_drawdown_p95"] >= hold["montecarlo"]["max_drawdown_p50"]


def test_cli_kronos_reports_missing_forecasts(tmp_path, capsys):
    code = main(
        ["kronos", "--forecasts-dir", str(tmp_path / "nada"), "--out-dir", str(tmp_path / "o")]
    )
    assert code == 2
    assert "faltan pronósticos" in capsys.readouterr().err


def test_cli_kronos_writes_json(kronos_env, tmp_path, monkeypatch):
    import tm_engine.lab.kronos as kronos_mod

    monkeypatch.setattr(kronos_mod, "store_bar_loader", lambda _: kronos_env["bar_loader"])
    out = tmp_path / "out"
    args = ["kronos", "--symbols", "BTCUSDT", "--forecasts-dir", str(kronos_env["root"])]
    code = main([*args, "--out-dir", str(out), "--n-sims", "100", "--engine", "vector"])
    assert code == 0
    data = json.loads((out / "kronos.json").read_text())
    assert data["holdout"] is None and "min_return" in data["chosen"]
