from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError
from tm_shared.contracts import (
    Direction,
    Horizon,
    OrderSide,
    Rationale,
    RiskVerdict,
    Signal,
    SimResult,
    Timeframe,
    TradeDecision,
)


def make_signal(**kw):
    base = dict(
        symbol="BTCUSDT",
        direction=Direction.LONG,
        confidence=0.7,
        horizon=Horizon.MEDIUM,
        timeframe=Timeframe.D1,
        source="trend",
    )
    return Signal(**(base | kw))


def make_sim(**kw):
    base = dict(
        p_take_profit=0.5,
        p_stop_loss=0.3,
        p_open=0.2,
        expected_value=0.2,
        n_paths=100,
        source="combined",
    )
    return SimResult(**(base | kw))


class TestSignal:
    def test_score_has_sign(self):
        assert make_signal().score == pytest.approx(0.7)
        assert make_signal(direction=Direction.SHORT).score == pytest.approx(-0.7)
        assert make_signal(direction=Direction.FLAT).score == 0.0

    @pytest.mark.parametrize("conf", [-0.1, 1.1])
    def test_confidence_range(self, conf):
        with pytest.raises(ValidationError):
            make_signal(confidence=conf)

    def test_symbol_format(self):
        with pytest.raises(ValidationError):
            make_signal(symbol="btc/usdt")

    def test_naive_datetime_rejected(self):
        with pytest.raises(ValidationError):
            make_signal(generated_at=datetime(2026, 1, 1))

    def test_frozen_and_no_extra_fields(self):
        s = make_signal()
        with pytest.raises(ValidationError):
            s.confidence = 0.1
        with pytest.raises(ValidationError):
            make_signal(unknown="x")

    def test_json_roundtrip(self):
        s = make_signal(metadata={"lookbacks": [20, 50]})
        assert Signal.model_validate_json(s.model_dump_json()) == s
        assert s.model_dump(mode="json")["direction"] == "long"


class TestSimResult:
    def test_probabilities_must_sum_to_one(self):
        with pytest.raises(ValidationError, match="sumar 1"):
            make_sim(p_open=0.5)

    def test_ok(self):
        assert make_sim().n_paths == 100


class TestTradeDecision:
    def test_buy_levels(self):
        d = TradeDecision(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=Decimal("0.01"),
            entry_price=Decimal("100"),
            stop_loss=Decimal("95"),
            take_profit=Decimal("110"),
        )
        assert d.risk_per_unit == Decimal("5")

    def test_sell_levels(self):
        d = TradeDecision(
            symbol="ETHUSDT",
            side=OrderSide.SELL,
            quantity=Decimal("1"),
            entry_price=Decimal("100"),
            stop_loss=Decimal("105"),
            take_profit=Decimal("90"),
        )
        assert d.risk_per_unit == Decimal("5")

    def test_risk_unknown_without_entry(self):
        d = TradeDecision(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            stop_loss=Decimal("95"),
            take_profit=Decimal("110"),
        )
        assert d.risk_per_unit is None

    @pytest.mark.parametrize(
        "side,entry,sl,tp",
        [
            (OrderSide.BUY, None, "110", "95"),
            (OrderSide.BUY, "120", "95", "110"),
            (OrderSide.SELL, None, "90", "105"),
            (OrderSide.SELL, "80", "105", "90"),
        ],
    )
    def test_inconsistent_levels(self, side, entry, sl, tp):
        with pytest.raises(ValidationError):
            TradeDecision(
                symbol="BTCUSDT",
                side=side,
                quantity=Decimal("1"),
                entry_price=Decimal(entry) if entry else None,
                stop_loss=Decimal(sl),
                take_profit=Decimal(tp),
            )

    def test_quantity_positive(self):
        with pytest.raises(ValidationError):
            TradeDecision(
                symbol="BTCUSDT",
                side=OrderSide.BUY,
                quantity=Decimal("0"),
                stop_loss=Decimal("95"),
                take_profit=Decimal("110"),
            )

    def test_rationale_roundtrip(self):
        r = Rationale(
            signals=[make_signal()],
            weights={"trend": 1.0},
            sim=make_sim(),
            risk=RiskVerdict(approved=True),
        )
        d = TradeDecision(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=Decimal("0.5"),
            stop_loss=Decimal("95"),
            take_profit=Decimal("110"),
            rationale=r,
        )
        assert TradeDecision.model_validate_json(d.model_dump_json()) == d
