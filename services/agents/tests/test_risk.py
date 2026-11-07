from decimal import Decimal as D

import pytest
from tm_agents.risk import Portfolio, RiskAgent, RiskLimits, TradeProposal
from tm_shared.contracts import OrderSide, Rationale, SimResult


def portfolio(**kw):
    base = dict(
        equity=D("10000"), peak_equity=D("10000"), day_start_equity=D("10000"), cash=D("10000")
    )
    return Portfolio(**(base | kw))


def proposal(**kw):
    base = dict(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        entry_price=D("100"),
        stop_loss=D("95"),
        take_profit=D("110"),
    )
    return TradeProposal(**(base | kw))


def agent(level=3, dd=0.2, **kw):
    return RiskAgent(RiskLimits.from_profile(level, dd, **kw))


@pytest.mark.parametrize("level,pct", [(1, "0.0025"), (3, "0.01"), (5, "0.02")])
def test_risk_table(level, pct):
    assert RiskLimits.from_profile(level, 0.2).risk_per_trade == D(pct)


@pytest.mark.parametrize("level,dd", [(0, 0.2), (6, 0.2), (3, 0), (3, 1)])
def test_invalid_profile(level, dd):
    with pytest.raises(ValueError):
        RiskLimits.from_profile(level, dd)


def test_pass_sizes_to_one_percent_risk():
    decision, verdict = agent().evaluate(proposal(), portfolio())
    assert verdict.approved and verdict.reasons == ["PASS"]
    # 1 % de 10,000 = 100 USDT de riesgo; 5 por unidad -> 20 unidades
    assert decision.quantity == D("20")
    assert decision.quantity * (decision.entry_price - decision.stop_loss) == D("100")
    assert decision.rationale.risk == verdict


def test_exposure_cap():
    # riesgo nivel 3: exposición máx 50 % = 5,000 -> 50 unidades a 100; con stop muy cerca
    decision, _ = agent().evaluate(proposal(stop_loss=D("99.9"), take_profit=D("101")), portfolio())
    assert decision.quantity == D("50")


def test_cash_cap():
    decision, _ = agent(level=5).evaluate(
        proposal(stop_loss=D("99.9"), take_profit=D("101")), portfolio(cash=D("1000"))
    )
    assert decision.quantity == D("10")


def test_quantity_rounds_down_to_step():
    decision, _ = agent().evaluate(
        proposal(entry_price=D("97"), stop_loss=D("94"), take_profit=D("103")), portfolio()
    )
    assert decision.quantity == D("33.33333")


@pytest.mark.parametrize(
    "prop_kw,pf_kw,reason",
    [
        (
            {},
            {"equity": D("7900"), "peak_equity": D("10000"), "day_start_equity": D("7900")},
            "kill switch",
        ),
        ({}, {"equity": D("9600")}, "pérdida del día"),
        ({}, {"trades_today": 6}, "operaciones hoy"),
        (
            {"side": OrderSide.SELL, "stop_loss": D("105"), "take_profit": D("90")},
            {},
            "no se abren cortos",
        ),
        ({}, {"open_symbols": frozenset({"BTCUSDT"})}, "posición abierta"),
        ({"take_profit": D("104")}, {}, "reward/risk"),
        ({"stop_loss": D("100")}, {}, "stop igual"),
        ({"rationale": Rationale(news_veto=True)}, {}, "noticias"),
        (
            {
                "rationale": Rationale(
                    sim=SimResult(
                        p_take_profit=0.2,
                        p_stop_loss=0.7,
                        p_open=0.1,
                        expected_value=-0.3,
                        n_paths=100,
                        source="combined",
                    )
                )
            },
            {},
            "valor esperado",
        ),
        ({}, {"cash": D("5")}, "nocional"),
    ],
)
def test_blocks(prop_kw, pf_kw, reason):
    decision, verdict = agent().evaluate(proposal(**prop_kw), portfolio(**pf_kw))
    assert decision is None and not verdict.approved
    assert any(reason in r for r in verdict.reasons), verdict.reasons


def test_multiple_reasons_reported():
    _, verdict = agent().evaluate(proposal(take_profit=D("101")), portfolio(trades_today=9))
    assert len(verdict.reasons) == 2


def test_shorts_allowed_when_enabled():
    decision, verdict = agent(allow_short=True).evaluate(
        proposal(side=OrderSide.SELL, stop_loss=D("105"), take_profit=D("90")), portfolio()
    )
    assert verdict.approved and decision.quantity == D("20")


def test_drawdown_helpers():
    a = agent(dd=0.1)
    p = portfolio(equity=D("9000"), peak_equity=D("10000"))
    assert a.drawdown(p) == D("0.1") and a.check_drawdown(p)
    assert a.drawdown(portfolio(peak_equity=D("0"))) == 0
    assert a.daily_loss(portfolio(day_start_equity=D("0"))) == 0
    assert a.position_size(D("100"), D("100"), portfolio()) == 0
