"""Fixtures compartidas: respuestas grabadas de Binance y utilidades sin red."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_json(name: str):
    return json.loads((FIXTURES / name).read_text())


def make_zip(csv_text: str, name: str = "data.csv") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, csv_text)
    return buffer.getvalue()


def kline_csv(open_times: list[int], step: int, header: bool = False) -> str:
    """CSV estilo data.binance.vision; `step` en las mismas unidades que `open_times`."""
    lines = []
    if header:
        lines.append(
            "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
            "taker_buy_volume,taker_buy_quote_volume,ignore"
        )
    for i, t in enumerate(open_times):
        p = 100 + i
        lines.append(f"{t},{p},{p + 1},{p - 1},{p + 0.5},10,{t + step - 1},1000,5,4,400,0")
    return "\n".join(lines) + "\n"


@pytest.fixture
def no_sleep():
    calls: list[float] = []
    return calls.append, calls
