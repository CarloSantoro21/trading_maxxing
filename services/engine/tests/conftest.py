"""Fixtures del engine: velas sintéticas pequeñas y decisiones de prueba."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import numpy as np
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


# --- laboratorio (#13) ---------------------------------------------------------------------


def _ohlcv(index: pd.DatetimeIndex, close, volume=1000.0) -> pd.DataFrame:
    close = np.asarray(close, dtype=float)
    opens = np.r_[close[0], close[:-1]]
    return pd.DataFrame(
        {
            "open": opens,
            "high": np.maximum(opens, close) * 1.002,
            "low": np.minimum(opens, close) * 0.998,
            "close": close,
            "volume": volume,
        },
        index=pd.DatetimeIndex(index, name="open_time"),
    )


@pytest.fixture
def daily_bars():
    """Velas diarias sintéticas (índice UTC); `price(t)` da el cierre del día `t`."""

    def _make(start: str, end: str, price) -> pd.DataFrame:
        idx = pd.date_range(start, end, freq="1D", tz="UTC", inclusive="left")
        return _ohlcv(idx, price(np.arange(len(idx))))

    return _make


@pytest.fixture
def intraday_bars():
    """Velas de 30 min sintéticas: ruido con semilla fija y más volumen desde las 9:30 ET."""

    def _make(start: str, end: str, seed: int = 0, sigma: float = 0.003) -> pd.DataFrame:
        idx = pd.date_range(start, end, freq="30min", tz="UTC", inclusive="left")
        rng = np.random.default_rng(seed)
        close = np.round(100.0 * np.cumprod(1.0 + rng.normal(0.0, sigma, len(idx))), 2)
        et = idx.tz_convert("America/New_York")
        slot = np.asarray(et.hour * 2 + et.minute // 30)
        volume = np.where((slot >= 19) & (slot < 34), 3e6, 1e6)
        return _ohlcv(idx, close, volume)

    return _make


class FramesLoader:
    """Cargador en memoria que registra los rangos pedidos (para probar la guarda del holdout)."""

    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self.frames = frames
        self.calls: list[tuple[str, str, pd.Timestamp, pd.Timestamp]] = []

    def __call__(self, symbol, tf, start, end):
        self.calls.append((symbol, tf, start, end))
        f = self.frames[symbol]
        return f[(f.index >= start) & (f.index < end)]


@pytest.fixture
def frames_loader():
    return FramesLoader


# --- orquestador en el reloj del motor y tabla trades en memoria (#89) -------------------


class LiveOrchestration:
    """Fuente de decisiones que corre el orquestador en el reloj del motor (como en vivo).

    En cada `poll(now)` del motor: aplica los `hooks` (p. ej. el LifecycleMonitor), corre la
    capa compartida en ese cierre de vela y le pasa los eventos a los runners. Devuelve las
    decisiones reales; las de shadow se quedan en su sink.
    """

    def __init__(self, layer, runners, *, start=None, hooks=()) -> None:
        self.layer = layer
        self.runners = list(runners)
        self.start = start
        self.hooks = list(hooks)
        self.events = []
        self.decisions = []

    def poll(self, now):
        if self.start is not None and now < self.start:
            return []
        for hook in self.hooks:
            hook(now, self)
        out = []
        for event in self.layer.on_bar_close(now):
            self.events.append(event)
            for runner in self.runners:
                out.extend(e for e in runner.on_event(event) if not e.shadow)
        self.decisions.extend(out)
        return out


@pytest.fixture
def live_orchestration():
    return LiveOrchestration


class FakeTradesTable:
    """PostgREST mínimo para `public.trades` (POST, PATCH por id y GET con filtros), sin red."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if request.method == "POST":
            row = {**json.loads(request.content), "id": f"t-{len(self.rows) + 1}"}
            row.setdefault("closed_at", None)
            row.setdefault("pnl", None)
            self.rows.append(row)
            return httpx.Response(201, json=[row])
        if request.method == "PATCH":
            trade_id = params["id"].removeprefix("eq.")
            row = next(r for r in self.rows if r["id"] == trade_id)
            row.update(json.loads(request.content))
            return httpx.Response(200, json=[row])
        rows = [r for r in self.rows if self._match(r, params)]
        offset, limit = int(params.get("offset", 0)), int(params.get("limit", 10**9))
        return httpx.Response(200, json=rows[offset : offset + limit])

    @staticmethod
    def _match(row: dict, params: dict) -> bool:
        for col, cond in params.items():
            if col in ("select", "order", "limit", "offset"):
                continue
            op, _, value = cond.partition(".")
            if op == "eq" and str(row.get(col)) != value:
                return False
            if op == "is" and value == "null" and row.get(col) is not None:
                return False
        return True

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


@pytest.fixture
def trades_table():
    return FakeTradesTable()
