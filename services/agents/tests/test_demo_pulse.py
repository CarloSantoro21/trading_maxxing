"""DemoPulseAgent: estrategia solo para la demo local (TM_DEMO_STRATEGY=1)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from tm_agents import __main__ as worker
from tm_agents.base import AgentContext, registered_strategies
from tm_agents.orchestrator.bot import BotConfig, BotProfile
from tm_agents.orchestrator.policy import policy_for
from tm_agents.orchestrator.shared import FrameBarsProvider, SharedSignalLayer
from tm_agents.orchestrator.streams import MemorySink
from tm_agents.orchestrator.supervisor import BotSupervisor, InMemoryBotRepository
from tm_agents.registry import InMemoryBackend, StrategyRegistry
from tm_agents.simulation_gate import GateConfig, SimulationGate
from tm_agents.strategies.demo_pulse import DemoPulseAgent, pulse_is_long, pulse_minutes
from tm_agents.strategy_stats import StrategyState
from tm_shared.contracts import Direction, Horizon, OrderSide, Timeframe

ROOT = Path(__file__).resolve().parents[3]
# 2026-11-22 12:00 UTC: minuto de época múltiplo de 4, así que con pulso de 2 min es "long".
T0 = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
REPORT = {
    "aggregate": {"volatility": 0.4, "sharpe": 1.0},
    "montecarlo": {"max_drawdown": {"p95": 0.15, "p99": 0.25}},
}


def minute_bars(n: int = 600, end: datetime = T0) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    close = 60_000 * np.cumprod(1 + rng.normal(0, 0.0005, n))
    idx = pd.date_range(end - timedelta(minutes=n), periods=n, freq="1min", tz="UTC")
    return pd.DataFrame(
        {"open": close, "high": close * 1.0005, "low": close * 0.9995, "close": close, "volume": 1},
        index=idx.rename("open_time"),
    )


def test_pulse_alternates_every_n_minutes(monkeypatch):
    assert pulse_is_long(pd.Timestamp(T0), 2)
    assert pulse_is_long(pd.Timestamp(T0 + timedelta(minutes=1)), 2)
    assert not pulse_is_long(pd.Timestamp(T0 + timedelta(minutes=2)), 2)
    assert not pulse_is_long(pd.Timestamp(T0 + timedelta(minutes=3)), 2)
    assert pulse_is_long(pd.Timestamp(T0 + timedelta(minutes=4)), 2)
    monkeypatch.delenv("TM_DEMO_PULSE_MINUTES", raising=False)
    assert pulse_minutes() == 2
    monkeypatch.setenv("TM_DEMO_PULSE_MINUTES", "5")
    assert pulse_minutes() == 5 and DemoPulseAgent().minutes == 5
    assert pulse_minutes(3) == 3
    with pytest.raises(ValueError):
        pulse_minutes(0)
    with pytest.raises(ValueError):
        DemoPulseAgent(minutes=2, confidence=0)


def test_signal_only_for_its_symbols():
    agent = DemoPulseAgent(minutes=2)
    bars = {Timeframe.M1: minute_bars()}
    sig = agent.analyze(AgentContext("BTCUSDT", T0, bars))
    assert sig.direction is Direction.LONG and sig.confidence == 1.0
    assert sig.timeframe is Timeframe.M1 and sig.source == "demo_pulse"
    assert sig.metadata["demo"] is True and sig.metadata["phase"] == "long"
    flat = agent.analyze(AgentContext("BTCUSDT", T0 + timedelta(minutes=2), bars))
    assert flat.direction is Direction.FLAT and flat.confidence == 0.0
    eth = agent.analyze(AgentContext("ETHUSDT", T0, bars))
    assert eth.direction is Direction.FLAT
    empty = agent.analyze(AgentContext("BTCUSDT", T0, {Timeframe.M1: minute_bars().iloc[:0]}))
    assert empty.direction is Direction.FLAT


def test_trajectories_are_synthetic_and_rising():
    agent = DemoPulseAgent(minutes=2)
    bars = minute_bars()
    paths = agent.trajectories(AgentContext("BTCUSDT", T0, {Timeframe.M1: bars}))
    assert paths.shape == (200, 5, 4)
    assert np.median(paths[:, -1, 3]) > float(bars["close"].iloc[-1]) * 1.015
    policy = policy_for("demo_pulse")
    assert policy.use_external_paths and policy.gate_horizon == 5


def test_flows_through_gate_bot_and_risk_to_decisions():
    """Capa compartida -> SimulationGate -> BotRunner (allocator + RiskAgent) -> decisiones."""
    registry = StrategyRegistry(InMemoryBackend())
    registry.register("demo_pulse")
    registry.backend.update("demo_pulse", {"state": StrategyState.ACTIVE, "lab_report": REPORT})
    frames = {
        ("BTCUSDT", Timeframe.M1): minute_bars(n=900, end=T0 + timedelta(minutes=10)),
        ("ETHUSDT", Timeframe.M1): minute_bars(n=900, end=T0 + timedelta(minutes=10)) / 20,
    }
    signals = MemorySink()
    layer = SharedSignalLayer(
        registry,
        FrameBarsProvider(frames),
        signals,
        gate=SimulationGate(GateConfig(n_paths=500, fee_rate=0.001)),
        agents={"demo_pulse": DemoPulseAgent(minutes=2)},
    )
    profile = BotProfile(
        risk_level=3, capital=Decimal("10000"), max_drawdown=0.2, horizon=Horizon.MEDIUM
    )
    decisions = MemorySink()
    supervisor = BotSupervisor(
        InMemoryBotRepository([BotConfig("bot-1", profile)]),
        decisions,
        risk_overrides={"max_trades_per_day": 10_000},
    )
    supervisor.sync()
    for minute in range(9):
        for event in layer.on_bar_close(T0 + timedelta(minutes=minute)):
            supervisor.on_event(event)
    btc = [e for e in signals.items if e.symbol == "BTCUSDT"]
    assert btc[0].sim is not None and btc[0].sim.expected_value > 0
    sides = [d.decision.side for d in decisions.items]
    # long en 0-1, flat en 2-3, long en 4-5, flat en 6-7, long en 8: compra, venta, ...
    assert sides == [OrderSide.BUY, OrderSide.SELL, OrderSide.BUY, OrderSide.SELL, OrderSide.BUY]
    first = decisions.items[0]
    assert first.bot_id == "bot-1" and first.sleeve == "demo_pulse"
    assert first.decision.symbol == "BTCUSDT" and first.decision.quantity > 0
    assert first.decision.rationale.risk is not None and first.decision.rationale.risk.approved
    runner = supervisor.runners["bot-1"]
    assert runner.limits.max_trades_per_day == 10_000


def test_registered_only_with_the_flag(monkeypatch):
    assert worker.register_demo_strategy({}) is False
    assert worker.register_demo_strategy({"TM_DEMO_STRATEGY": "0"}) is False
    assert worker.demo_risk_overrides({}) == {}
    assert worker.register_demo_strategy({"TM_DEMO_STRATEGY": "1"}) is True
    assert "demo_pulse" in registered_strategies()
    assert worker.demo_risk_overrides({"TM_DEMO_STRATEGY": "1"}) == {"max_trades_per_day": 10_000}
    env = {"TM_DEMO_STRATEGY": "true", "TM_DEMO_MAX_TRADES_PER_DAY": "50"}
    assert worker.demo_risk_overrides(env) == {"max_trades_per_day": 50}
    # El paquete de estrategias no la importa: sin la bandera no hay código para la fila.
    import tm_agents.strategies as library

    assert "DemoPulseAgent" not in library.__all__


def test_demo_strategy_only_in_the_local_seed():
    """La fila demo_pulse vive en el seed local, nunca en las migraciones (dev y prod)."""
    api = ROOT / "apps" / "api"
    migrations = sorted((api / "migrations").glob("*.sql"))
    assert migrations
    db = sqlite3.connect(":memory:")
    for migration in migrations:
        assert "demo_pulse" not in migration.read_text(), migration.name
        db.executescript(migration.read_text())
    # Solo con las migraciones (lo que corre en tm-dev y tm-prod) no existe.
    count = "SELECT count(*) FROM strategies WHERE name = 'demo_pulse'"
    assert db.execute(count).fetchone() == (0,)
    db.executescript((api / "seed" / "seed.sql").read_text())
    state, params = db.execute(
        "SELECT state, params FROM strategies WHERE name = 'demo_pulse'"
    ).fetchone()
    assert state == "active" and json.loads(params) == {"symbols": ["BTCUSDT"]}
    # El bot del seed empieza detenido: en la demo se arranca desde el dashboard.
    assert db.execute("SELECT status FROM bots").fetchall() == [("stopped",)]
