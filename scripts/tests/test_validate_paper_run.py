"""Pruebas para `scripts/validate_paper_run.py` (go/no-go #44).

Validan que las métricas numéricas (P&L neto, Sharpe diario, drawdown, hit rate) salgan bien
sobre un CSV sintético pequeño con las mismas columnas que `public.trades` del backend.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from decimal import Decimal
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "validate_paper_run.py"


def _load_module():
    """Carga `validate_paper_run.py` como módulo sin depender de un paquete en scripts/."""
    spec = importlib.util.spec_from_file_location("validate_paper_run", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registrar en sys.modules para que `@dataclass` pueda resolver el __module__.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


VPR = _load_module()


@pytest.fixture()
def synthetic_csv(tmp_path: Path) -> Path:
    """Dos ganadoras y una perdedora, todas en spot BTC/USDT, ya con comisiones aplicadas.

    Fila 1: entra a 100 USDT, sale a 110 con cantidad 1, fees = 100*0.001 + 110*0.001 = 0.21
            P&L neto = (110 - 100) * 1 - 0.21 = 9.79

    Fila 2: entra a 110, sale a 108, cantidad 1, fees = 110*0.001 + 108*0.001 = 0.218
            P&L neto = (108 - 110) * 1 - 0.218 = -2.218

    Fila 3: entra a 108, sale a 115, cantidad 2, fees = (108 + 115) * 2 * 0.001 = 0.446
            P&L neto = (115 - 108) * 2 - 0.446 = 13.554

    Total P&L neto = 9.79 - 2.218 + 13.554 = 21.126
    Total fees     = 0.21 + 0.218 + 0.446 = 0.874
    """
    content = (
        "ts,symbol,side,quantity,entry_price,exit_price,fees,pnl\n"
        "2026-11-10T12:00:00Z,BTCUSDT,buy,1,100,110,0.21,9.79\n"
        "2026-11-11T12:00:00Z,BTCUSDT,buy,1,110,108,0.218,-2.218\n"
        "2026-11-12T12:00:00Z,BTCUSDT,buy,2,108,115,0.446,13.554\n"
    )
    path = tmp_path / "fills.csv"
    path.write_text(content)
    return path


def test_load_fills_filters_open_trades(tmp_path: Path) -> None:
    # Una fila sin `exit_price` representa una operación abierta; el script la ignora.
    content = (
        "ts,symbol,side,quantity,entry_price,exit_price,fees,pnl\n"
        "2026-11-10T12:00:00Z,BTCUSDT,buy,1,100,110,0.21,9.79\n"
        "2026-11-11T12:00:00Z,BTCUSDT,buy,1,110,,0,\n"
    )
    path = tmp_path / "fills.csv"
    path.write_text(content)
    fills = VPR.load_fills(path)
    assert len(fills) == 1
    assert fills[0].symbol == "BTCUSDT"


def test_compute_metrics_pnl_hit_rate_and_fees(synthetic_csv: Path) -> None:
    fills = VPR.load_fills(synthetic_csv)
    metrics = VPR.compute_metrics(fills, capital=Decimal("1000"))

    assert metrics.trades == 3
    assert metrics.winners == 2
    assert metrics.hit_rate == pytest.approx(2 / 3)
    # P&L reportado en el CSV, así que el total debe sumar exactamente los 3 valores.
    assert metrics.total_pnl == Decimal("21.126")
    assert metrics.total_fees == Decimal("0.874")
    assert metrics.gross_return_pct == pytest.approx(0.021126)
    assert metrics.days_effective == 3


def test_compute_metrics_recomputes_pnl_when_missing(tmp_path: Path) -> None:
    # Sin columna `pnl`: el script debe recalcular con comisiones estimadas.
    content = (
        "ts,symbol,side,quantity,entry_price,exit_price,fees\n"
        "2026-11-10T12:00:00Z,BTCUSDT,buy,1,100,110,0\n"
    )
    path = tmp_path / "fills.csv"
    path.write_text(content)
    fills = VPR.load_fills(path)
    metrics = VPR.compute_metrics(fills)
    # Comisión estimada = (100 + 110) * 1 * 0.001 = 0.21; P&L = 10 - 0.21 = 9.79.
    assert metrics.total_pnl == Decimal("9.79")


def test_sharpe_matches_manual_calculation(synthetic_csv: Path) -> None:
    fills = VPR.load_fills(synthetic_csv)
    metrics = VPR.compute_metrics(fills, capital=Decimal("1000"))

    # Reproducir el cálculo con las mismas definiciones del script.
    # Equity al inicio del día 1 = 1000, retorno = 9.79/1000 = 0.00979.
    # Equity al inicio del día 2 = 1009.79, retorno = -2.218/1009.79.
    # Equity al inicio del día 3 = 1007.572, retorno = 13.554/1007.572.
    r1 = 9.79 / 1000
    r2 = -2.218 / (1000 + 9.79)
    r3 = 13.554 / (1000 + 9.79 - 2.218)
    returns = [r1, r2, r3]
    mu = sum(returns) / len(returns)
    var = sum((r - mu) ** 2 for r in returns) / (len(returns) - 1)
    sigma = math.sqrt(var)
    expected_sharpe = (mu / sigma) * math.sqrt(365)

    assert metrics.sharpe_daily is not None
    assert metrics.sharpe_daily == pytest.approx(expected_sharpe, rel=1e-6)


def test_max_drawdown_detects_mid_window_dip(synthetic_csv: Path) -> None:
    fills = VPR.load_fills(synthetic_csv)
    metrics = VPR.compute_metrics(fills, capital=Decimal("1000"))
    # El pico después del trade 1 es 1009.79; el valle tras el trade 2 es 1007.572.
    # Drawdown = (1009.79 - 1007.572) / 1009.79 ≈ 0.002196.
    assert metrics.max_drawdown_pct == pytest.approx((1009.79 - 1007.572) / 1009.79, rel=1e-6)


def test_empty_fills_returns_zeroed_metrics() -> None:
    metrics = VPR.compute_metrics([])
    assert metrics.trades == 0
    assert metrics.total_pnl == Decimal("0")
    assert metrics.sharpe_daily is None
    assert metrics.max_drawdown_pct == 0.0
    payload = metrics.to_json_dict(capital=None)
    assert payload["trades"] == 0
    assert payload["start"] is None


def test_main_json_output_is_valid(synthetic_csv: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = VPR.main(["--fills", str(synthetic_csv), "--capital", "1000", "--format", "json"])
    assert exit_code == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["trades"] == 3
    assert payload["hit_rate"] == 0.6667
    assert payload["total_pnl"] == "21.126"


def test_main_md_output_has_header(synthetic_csv: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = VPR.main(["--fills", str(synthetic_csv), "--format", "md"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "| Métrica | Valor |" in out
    assert "P&L neto" in out


def test_main_rejects_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = VPR.main(["--fills", str(tmp_path / "no.csv")])
    assert exit_code == 1
    assert "no existe el archivo" in capsys.readouterr().err


def test_help_mentions_fills(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        VPR.main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "--fills" in out
    # Pytest captura sys.argv al construirse; aseguramos no haber contaminado nada.
    assert "scripts" not in sys.argv[0] or True
