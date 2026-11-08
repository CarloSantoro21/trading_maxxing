"""RiskAgent: tamaño de posición, límites y kill switch. 100 % determinista.

Ninguna orden llega al motor sin pasar por `RiskAgent.evaluate`, que responde PASS o BLOCK
con las razones. El riesgo por operación, la exposición máxima y el drawdown máximo salen
del nivel de riesgo del perfil (1 a 5, :data:`RISK_TABLE`), y la cantidad se calcula para
que, si se toca el stop, la pérdida sea ese porcentaje del equity.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from decimal import ROUND_DOWN, Decimal
from types import MappingProxyType

from tm_shared.contracts import OrderSide, Rationale, RiskVerdict, TradeDecision

#: Capital mínimo de un portafolio, en USDT de testnet (propuesta, sección 3.4).
MIN_CAPITAL = Decimal("500")


@dataclass(frozen=True)
class RiskLevel:
    """Un nivel de riesgo del perfil y los límites que se derivan de él.

    Todas las proporciones son fracciones: ``Decimal("0.2")`` = 20 %.
    """

    level: int
    name: str
    #: Caída máxima tolerada desde el máximo de equity; al rebasarla salta el kill switch.
    max_drawdown: Decimal
    #: Porcentaje del equity que se pierde si una operación toca su stop.
    risk_per_trade: Decimal
    #: Nocional máximo de las posiciones abiertas como fracción del equity.
    max_exposure: Decimal

    def max_loss(self, capital: Decimal | float | int) -> Decimal:
        """Caída máxima en USDT para un capital dado (lo que muestra el onboarding)."""
        return Decimal(str(capital)) * self.max_drawdown


# Tabla única de niveles de riesgo (propuesta, sección 3.4). La copian, y deben coincidir:
#   * supabase/migrations/20261109000100_profiles_risk_levels.sql (private.risk_level_max_drawdown)
#   * apps/web/src/lib/onboarding/schema.ts (RISK_TABLE)
# tests/test_risk_levels_sync.py falla si alguna se desalinea.
RISK_TABLE: Mapping[int, RiskLevel] = MappingProxyType(
    {
        1: RiskLevel(1, "Conservador", Decimal("0.10"), Decimal("0.0025"), Decimal("0.20")),
        2: RiskLevel(2, "Prudente", Decimal("0.15"), Decimal("0.005"), Decimal("0.35")),
        3: RiskLevel(3, "Moderado", Decimal("0.20"), Decimal("0.01"), Decimal("0.50")),
        4: RiskLevel(4, "Decidido", Decimal("0.30"), Decimal("0.015"), Decimal("0.70")),
        5: RiskLevel(5, "Agresivo", Decimal("0.40"), Decimal("0.02"), Decimal("0.90")),
    }
)


def get_risk_level(level: int) -> RiskLevel:
    """Nivel de riesgo del 1 al 5; cualquier otro valor es un ``ValueError``."""
    try:
        return RISK_TABLE[level]
    except (KeyError, TypeError):
        raise ValueError("risk_level debe estar entre 1 y 5") from None


@dataclass(frozen=True)
class RiskLimits:
    risk_per_trade: Decimal
    max_exposure: Decimal
    max_drawdown: Decimal
    daily_loss_limit: Decimal
    max_trades_per_day: int = 6
    min_reward_risk: Decimal = Decimal("1.5")
    min_notional: Decimal = Decimal("10")  # USDT, mínimo de Binance Spot con margen
    qty_step: Decimal = Decimal("0.00001")
    allow_short: bool = False  # Binance Spot

    @classmethod
    def from_profile(
        cls, risk_level: int, max_drawdown: float | None = None, **overrides: object
    ) -> RiskLimits:
        """Límites del RiskAgent a partir del nivel de riesgo del perfil.

        El drawdown máximo sale de :data:`RISK_TABLE` (igual que ``profiles.max_drawdown``,
        que la base deriva del nivel). ``max_drawdown`` solo existe para forzar otro valor,
        p. ej. en pruebas o backtests; debe estar en (0, 1).
        """
        spec = get_risk_level(risk_level)
        if max_drawdown is None:
            drawdown = spec.max_drawdown
        elif 0 < max_drawdown < 1:
            drawdown = Decimal(str(max_drawdown))
        else:
            raise ValueError("max_drawdown debe estar entre 0 y 1")
        limits = cls(
            risk_per_trade=spec.risk_per_trade,
            max_exposure=spec.max_exposure,
            max_drawdown=drawdown,
            daily_loss_limit=spec.risk_per_trade * 3,
        )
        return replace(limits, **overrides) if overrides else limits


@dataclass(frozen=True)
class Portfolio:
    equity: Decimal
    peak_equity: Decimal
    day_start_equity: Decimal
    cash: Decimal
    exposure: Decimal = Decimal("0")  # nocional de posiciones abiertas
    trades_today: int = 0
    open_symbols: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class TradeProposal:
    """Lo que el orquestador quiere hacer, antes de saber cuánto."""

    symbol: str
    side: OrderSide
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    rationale: Rationale = field(default_factory=Rationale)


class RiskAgent:
    def __init__(self, limits: RiskLimits) -> None:
        self.limits = limits

    # --- límites del portafolio ---------------------------------------------------------
    def drawdown(self, p: Portfolio) -> Decimal:
        if p.peak_equity <= 0:
            return Decimal("0")
        return max(Decimal("0"), (p.peak_equity - p.equity) / p.peak_equity)

    def check_drawdown(self, p: Portfolio) -> bool:
        """True si se rebasó el drawdown máximo: el bot debe cerrar todo y detenerse."""
        return self.drawdown(p) >= self.limits.max_drawdown

    def daily_loss(self, p: Portfolio) -> Decimal:
        if p.day_start_equity <= 0:
            return Decimal("0")
        return max(Decimal("0"), (p.day_start_equity - p.equity) / p.day_start_equity)

    # --- tamaño -------------------------------------------------------------------------
    def position_size(self, entry: Decimal, stop: Decimal, p: Portfolio) -> Decimal:
        """Cantidad tal que tocar el stop cueste `risk_per_trade` del equity, con topes."""
        per_unit = abs(entry - stop)
        if per_unit == 0:
            return Decimal("0")
        qty = p.equity * self.limits.risk_per_trade / per_unit
        room = max(Decimal("0"), p.equity * self.limits.max_exposure - p.exposure)
        qty = min(qty, room / entry, p.cash / entry)
        return qty.quantize(self.limits.qty_step, rounding=ROUND_DOWN)

    # --- decisión -----------------------------------------------------------------------
    def evaluate(
        self, proposal: TradeProposal, p: Portfolio
    ) -> tuple[TradeDecision | None, RiskVerdict]:
        L = self.limits
        reasons: list[str] = []
        if self.check_drawdown(p):
            reasons.append(
                f"kill switch: drawdown {self.drawdown(p):.2%} >= máximo {L.max_drawdown:.2%}"
            )
        if self.daily_loss(p) >= L.daily_loss_limit:
            reasons.append(f"pérdida del día {self.daily_loss(p):.2%} >= {L.daily_loss_limit:.2%}")
        if p.trades_today >= L.max_trades_per_day:
            reasons.append(
                f"ya hubo {p.trades_today} operaciones hoy (máximo {L.max_trades_per_day})"
            )
        if proposal.side is OrderSide.SELL and not L.allow_short:
            reasons.append("spot: no se abren cortos")
        if proposal.symbol in p.open_symbols:
            reasons.append(f"ya hay una posición abierta en {proposal.symbol}")
        if proposal.rationale.news_veto:
            reasons.append("veto por noticias")
        sim = proposal.rationale.sim
        if sim is not None and sim.expected_value <= 0:
            reasons.append(f"valor esperado simulado {sim.expected_value:+.2f}R <= 0")

        risk = abs(proposal.entry_price - proposal.stop_loss)
        reward = abs(proposal.take_profit - proposal.entry_price)
        if risk == 0:
            reasons.append("stop igual a la entrada")
        elif reward / risk < L.min_reward_risk:
            reasons.append(f"reward/risk {reward / risk:.2f} < {L.min_reward_risk}")

        qty = (
            Decimal("0")
            if reasons
            else self.position_size(proposal.entry_price, proposal.stop_loss, p)
        )
        if not reasons and qty * proposal.entry_price < L.min_notional:
            reasons.append(
                f"nocional {qty * proposal.entry_price:.2f} < mínimo {L.min_notional} USDT"
            )

        verdict = RiskVerdict(approved=not reasons, reasons=reasons or ["PASS"])
        if reasons:
            return None, verdict
        decision = TradeDecision(
            symbol=proposal.symbol,
            side=proposal.side,
            quantity=qty,
            entry_price=proposal.entry_price,
            stop_loss=proposal.stop_loss,
            take_profit=proposal.take_profit,
            rationale=proposal.rationale.model_copy(update={"risk": verdict}),
        )
        return decision, verdict
