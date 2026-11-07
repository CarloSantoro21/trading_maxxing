"""Fixtures del engine: velas sintéticas pequeñas y decisiones de prueba."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pandas as pd
import pytest
from tm_engine.decisions import DecisionEnvelope
from tm_shared.contracts import OrderSide, Rationale, RiskVerdict, TradeDecision

T0 = datetime(2024, 1, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _no_binance_keys(monkeypatch):
    """Ninguna prueba debe ver llaves reales del entorno del desarrollador."""
    for var in ("BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET", "REDIS_URL"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def t0() -> datetime:
    return T0


@pytest.fixture
def make_frame():
    """DataFrame como el de `MarketDataService.get_bars` (índice `open_time`, velas de 1m).

    Cada vela abre en el cierre anterior y su rango es ±`spread` alrededor de open/close.
    """

    def _make(closes: list[float], spread: float = 0.5, start: datetime = T0) -> pd.DataFrame:
        opens = [closes[0], *closes[:-1]]
        index = pd.date_range(start, periods=len(closes), freq="1min", name="open_time")
        return pd.DataFrame(
            {
                "open": opens,
                "high": [max(o, c) + spread for o, c in zip(opens, closes, strict=True)],
                "low": [min(o, c) - spread for o, c in zip(opens, closes, strict=True)],
                "close": closes,
                "volume": [10.0] * len(closes),
            },
            index=index,
        )

    return _make


@pytest.fixture
def envelope():
    """Sobre de decisión; `minute` es el minuto (desde T0) en que el orquestador decide."""

    def _make(
        side: OrderSide = OrderSide.BUY,
        *,
        sleeve: str = "trend",
        symbol: str = "BTCUSDT",
        quantity: str = "1",
        stop_loss: str = "95",
        take_profit: str = "110",
        minute: int = 1,
        approved: bool | None = None,
    ) -> DecisionEnvelope:
        if side is OrderSide.SELL:
            stop_loss, take_profit = take_profit, stop_loss
        risk = None if approved is None else RiskVerdict(approved=approved, reasons=["límite"])
        decision = TradeDecision(
            symbol=symbol,
            side=side,
            quantity=Decimal(quantity),
            stop_loss=Decimal(stop_loss),
            take_profit=Decimal(take_profit),
            rationale=Rationale(risk=risk),
            decided_at=T0 + timedelta(minutes=minute),
        )
        return DecisionEnvelope(sleeve=sleeve, decision=decision)

    return _make
