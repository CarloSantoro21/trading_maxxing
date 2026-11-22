"""Ejecución simulada (`ENGINE_EXECUTION=sim`): precios públicos, comisión y registro por la API."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from tm_engine import __main__ as worker
from tm_engine.decisions import DecisionEnvelope
from tm_engine.recorder import MemoryFillRecorder, RestoredPosition, WorkerApiTradeRecorder
from tm_engine.service import (
    EngineMode,
    EngineProfile,
    ExecutionMode,
    QueueDecisionSource,
    TradingEngineService,
    resolve_execution,
)
from tm_engine.sim import PublicPriceFeed, SimExecutionEngine, SimRunner
from tm_shared.contracts import OrderSide

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)


class Prices:
    """Precio fijo y modificable, sin red."""

    def __init__(self, price: str | None = "100") -> None:
        self.value = None if price is None else Decimal(price)
        self.source = "fake"
        self.remembered: dict[str, Decimal] = {}

    def remember(self, symbol: str, price: Decimal) -> None:
        self.remembered[symbol] = price

    def price(self, symbol: str) -> Decimal | None:
        return self.value


def _with(env: DecisionEnvelope, **update) -> DecisionEnvelope:
    return env.model_copy(update=update)


def _engine(prices=None, recorder=None, **kw) -> SimExecutionEngine:
    return SimExecutionEngine(
        recorder=recorder or MemoryFillRecorder(),
        prices=prices or Prices(),
        clock=lambda: NOW,
        **kw,
    )


def test_buy_fills_at_market_with_lab_fee(envelope):
    rec = MemoryFillRecorder()
    sim = _engine(Prices("101.5"), rec)
    assert sim.on_decision(_with(envelope(quantity="2"), bot_id="bot-1"))
    (fill,) = rec.fills
    assert fill.side is OrderSide.BUY and fill.role == "entry"
    assert fill.price == Decimal("101.5") and fill.quantity == Decimal("2")
    assert fill.commission == Decimal("0.203")  # 2 * 101.5 * 0.1 %
    assert fill.realized_pnl == Decimal("-0.203")
    assert fill.bot_id == "bot-1" and fill.execution == "sim" and fill.ts == NOW
    assert fill.client_order_id.startswith("SIM-")
    assert sim.ledger.quantity("bot-1/trend", "BTCUSDT") == Decimal("2")
    assert sim.positions() == [
        {
            "bot_id": "bot-1",
            "sleeve": "trend",
            "symbol": "BTCUSDT",
            "quantity": "2.00000",
            "avg_price": "101.5",
        }
    ]


def test_round_trip_pnl_and_close(envelope):
    rec = MemoryFillRecorder()
    prices = Prices("100")
    sim = _engine(prices, rec)
    sim.on_decision(envelope(quantity="1"))
    prices.value = Decimal("110")
    assert sim.on_decision(envelope(OrderSide.SELL, quantity="1"))
    sell = rec.fills[-1]
    assert sell.role == "exit" and sell.position_after == 0
    assert sell.realized_pnl == Decimal("10") - Decimal("0.110")
    assert Decimal(sim.sleeve_report()["trend"]["realized_pnl"]) == Decimal("9.79")


def test_partial_sell_with_target_and_scale_in(envelope):
    rec = MemoryFillRecorder()
    sim = _engine(Prices("100"), rec)
    first = _with(envelope(quantity="1"), target_quantity=Decimal("1"))
    sim.on_decision(first)
    # Con target_quantity se permite agregar un tramo.
    assert sim.on_decision(_with(envelope(quantity="0.5"), target_quantity=Decimal("1.5")))
    partial = _with(envelope(OrderSide.SELL, quantity="0.4"), target_quantity=Decimal("1.1"))
    assert sim.on_decision(partial)
    assert rec.fills[-1].role == "reduce" and rec.fills[-1].quantity == Decimal("0.4")
    assert sim.ledger.quantity("trend", "BTCUSDT") == Decimal("1.1")
    # Venta mayor a lo que hay: cierra todo.
    close_all = _with(envelope(OrderSide.SELL, quantity="9"), target_quantity=Decimal("0"))
    assert sim.on_decision(close_all)
    assert rec.fills[-1].role == "exit" and rec.fills[-1].quantity == Decimal("1.1")


def test_rejections_match_the_real_engine(envelope):
    sim = _engine(sleeve_guard=lambda sleeve: sleeve != "kronos")
    assert not sim.on_decision(_with(envelope(), shadow=True))
    assert not sim.on_decision(envelope(sleeve="kronos"))
    assert not sim.on_decision(envelope(approved=False))
    assert not sim.on_decision(envelope(OrderSide.SELL))
    sim.on_decision(envelope())
    assert not sim.on_decision(envelope())  # sin target: no se agrega
    reasons = [r.reason for r in sim.rejections]
    assert reasons[0].startswith("decisión de una estrategia en shadow")
    assert reasons[1] == "sin órdenes reales: el sleeve no opera capital"
    assert reasons[2].startswith("bloqueada por RiskAgent")
    assert reasons[3].startswith("sin posición en el sleeve")
    assert reasons[4] == "el sleeve ya tiene una posición abierta en el símbolo"
    # Una venta (cierre) pasa aunque la guardia no deje entrar al sleeve.
    sim._sleeve_guard = lambda sleeve: False
    assert sim.on_decision(envelope(OrderSide.SELL))


def test_without_price_the_decision_is_rejected(envelope):
    sim = _engine(Prices(None))
    assert not sim.on_decision(envelope())
    assert sim.rejections[-1].reason == "sin precio de mercado para BTCUSDT"


def test_stop_loss_and_take_profit_are_watched(envelope):
    rec = MemoryFillRecorder()
    prices = Prices("100")
    sim = _engine(prices, rec)
    sim.on_decision(envelope(quantity="1", stop_loss="95", take_profit="110"))
    assert sim.check_protection() == 0
    prices.value = Decimal("94")
    assert sim.check_protection() == 1
    assert rec.fills[-1].role == "sl" and rec.fills[-1].price == Decimal("94")
    assert sim.check_protection() == 0  # ya no hay posición
    sim.on_decision(envelope(quantity="1"))
    prices.value = Decimal("111")
    assert sim.check_protection() == 1 and rec.fills[-1].role == "tp"


def test_restore_then_close(envelope):
    rec = MemoryFillRecorder()
    sim = _engine(Prices("120"), rec)
    restored = [
        RestoredPosition("bot-1", "trend", "BTCUSDT", Decimal("0.5"), Decimal("100")),
        RestoredPosition("bot-1", "kronos", "BTCUSDT", Decimal("0"), Decimal("100")),
    ]
    assert sim.restore_positions(restored) == 1
    assert sim.on_decision(_with(envelope(OrderSide.SELL, quantity="0.5"), bot_id="bot-1"))
    assert rec.fills[-1].realized_pnl == Decimal("10") - Decimal("0.06")


def test_fills_are_recorded_through_the_api(envelope, trades_table):
    recorder = WorkerApiTradeRecorder(client=trades_table.client())
    prices = Prices("100")
    sim = _engine(prices, recorder)
    sim.on_decision(_with(envelope(quantity="1"), bot_id="bot-1"))
    (row,) = trades_table.rows
    assert row["bot_id"] == "bot-1" and row["closed_at"] is None
    assert row["rationale"]["execution"] == "sim" and row["rationale"]["sleeve"] == "trend"
    assert Decimal(str(row["fees"])) == Decimal("0.1")
    prices.value = Decimal("105")
    sim.on_decision(_with(envelope(OrderSide.SELL, quantity="1"), bot_id="bot-1"))
    (row,) = trades_table.rows
    assert row["closed_at"] is not None
    assert Decimal(str(row["exit_price"])) == Decimal("105")
    assert Decimal(str(row["pnl"])) == Decimal("5") - Decimal("0.205")


def _feed(handler, **kw) -> PublicPriceFeed:
    return PublicPriceFeed(client=httpx.Client(transport=httpx.MockTransport(handler)), **kw)


def test_price_feed_falls_back_to_binance_vision_and_last_known():
    calls: list[str] = []
    state = {"down": False}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        assert request.url.path == "/api/v3/ticker/price"
        assert request.url.params["symbol"] in ("BTCUSDT", "ETHUSDT")
        if state["down"]:
            raise httpx.ConnectError("sin red")
        if request.url.host == "api.binance.com":
            return httpx.Response(451, text="restricted location")
        return httpx.Response(200, json={"symbol": "BTCUSDT", "price": "60893.27000000"})

    feed = _feed(handler, ttl=0)
    assert feed.price("BTCUSDT") == Decimal("60893.27")
    assert calls == ["api.binance.com", "data-api.binance.vision"]
    assert feed.source == "https://data-api.binance.vision"
    state["down"] = True
    assert feed.price("BTCUSDT") == Decimal("60893.27")
    assert feed.source == "last_known"
    assert feed.price("ETHUSDT") is None
    feed.remember("ETHUSDT", Decimal("3000"))
    assert feed.last_known("ETHUSDT") == Decimal("3000")
    assert feed.price("ETHUSDT") == Decimal("3000")
    feed.close()


def test_price_feed_caches_within_ttl():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        return httpx.Response(200, json={"price": "10"})

    clock = {"t": 0.0}
    feed = _feed(handler, ttl=5, clock=lambda: clock["t"])
    assert feed.price("BTCUSDT") == Decimal("10")
    assert feed.price("BTCUSDT") == Decimal("10")
    assert len(calls) == 1
    clock["t"] = 6
    feed.price("BTCUSDT")
    assert len(calls) == 2


def test_runner_polls_the_source(envelope):
    queue = QueueDecisionSource()
    rec = MemoryFillRecorder()
    sim = SimExecutionEngine(queue, rec, prices=Prices("100"))
    runner = SimRunner(sim, interval=0.01, protect_every=0.01)
    runner.start()
    queue.push(envelope())
    deadline = time.monotonic() + 5
    while not rec.fills and time.monotonic() < deadline:
        time.sleep(0.01)
    runner.stop()
    assert len(rec.fills) == 1 and runner.error is None


def test_service_in_sim_mode(envelope):
    rec = MemoryFillRecorder()
    svc = TradingEngineService(
        credentials=None, recorder=rec, execution="sim", price_feed=Prices("100")
    )
    assert svc.start(EngineProfile(poll_interval_secs=0.01)) is EngineMode.SIM
    assert svc.strategy is None and svc.sim is not None
    svc.submit(envelope().decision, sleeve="trend")
    deadline = time.monotonic() + 5
    while not rec.fills and time.monotonic() < deadline:
        time.sleep(0.01)
    status = svc.status()
    assert status["mode"] == "sim" and status["connected"] is False and status["error"] is None
    assert status["sim"]["fills"] == 1 and status["sim"]["fee"] == "0.001"
    assert svc.get_positions()["sleeves"]["trend"]["positions"]["BTCUSDT"]["quantity"] == "1.00000"
    svc.stop()
    assert svc.mode is EngineMode.STOPPED


def test_service_forced_offline():
    svc = TradingEngineService(credentials=None, execution=ExecutionMode.OFFLINE)
    assert svc.start() is EngineMode.OFFLINE
    assert "offline" in svc.status()["error"]
    svc.stop()


def test_resolve_execution(monkeypatch):
    assert resolve_execution({}) is ExecutionMode.SIM
    assert resolve_execution({"ENGINE_EXECUTION": "auto"}) is ExecutionMode.SIM
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "k")
    monkeypatch.setenv("BINANCE_TESTNET_API_SECRET", "s")
    assert resolve_execution({}) is ExecutionMode.TESTNET
    assert resolve_execution({"ENGINE_EXECUTION": " SIM "}) is ExecutionMode.SIM
    assert resolve_execution({"ENGINE_EXECUTION": "offline"}) is ExecutionMode.OFFLINE
    with pytest.raises(ValueError, match="ENGINE_EXECUTION"):
        resolve_execution({"ENGINE_EXECUTION": "papel"})


def test_main_once_reports_sim_without_keys(capsys, monkeypatch):
    monkeypatch.delenv("ENGINE_EXECUTION", raising=False)
    worker.main(["--once"])
    assert '"mode": "sim"' in capsys.readouterr().out
