from datetime import UTC, datetime

import pandas as pd
import pytest
from tm_agents import base
from tm_agents.base import AgentContext, BaseAgent, build_strategies, register_strategy
from tm_shared.contracts import Direction, Horizon, Signal, Timeframe


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch):
    monkeypatch.setattr(base, "_REGISTRY", {})


def make_ctx():
    bars = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
    return AgentContext(
        symbol="BTCUSDT", now=datetime(2026, 1, 1, tzinfo=UTC), bars={Timeframe.D1: bars}
    )


def define_dummy():
    @register_strategy("dummy")
    class Dummy(BaseAgent):
        timeframe = Timeframe.D1

        def analyze(self, ctx):
            last = ctx.bars_for(self.timeframe)["close"].iloc[-1]
            return Signal(
                symbol=ctx.symbol,
                direction=Direction.LONG,
                confidence=min(1.0, last / 10 * self.params.get("scale", 1)),
                horizon=Horizon.MEDIUM,
                timeframe=self.timeframe,
                source=self.name,
            )

    return Dummy


def test_register_and_build():
    dummy = define_dummy()
    assert base.registered_strategies() == {"dummy": dummy}
    agents = build_strategies({"dummy": {"enabled": True, "scale": 2}})
    assert len(agents) == 1 and agents[0].params == {"scale": 2}
    sig = agents[0].analyze(make_ctx())
    assert sig.source == "dummy" and sig.confidence == pytest.approx(0.6)


def test_disabled_strategies_are_skipped():
    define_dummy()
    assert build_strategies({"dummy": {"enabled": False}}) == []


def test_unknown_strategy():
    with pytest.raises(KeyError, match="desconocida"):
        build_strategies({"nope": {}})


def test_duplicate_name_rejected():
    define_dummy()
    with pytest.raises(ValueError, match="ya hay"):
        define_dummy()


def test_non_agent_rejected():
    with pytest.raises(TypeError):
        register_strategy("x")(object)


def test_missing_timeframe_in_context():
    with pytest.raises(KeyError, match="15m"):
        make_ctx().bars_for(Timeframe.M15)


def test_base_agent_is_abstract():
    with pytest.raises(TypeError):
        BaseAgent()
