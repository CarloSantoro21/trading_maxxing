"""LifecycleMonitor: detecta cuándo una estrategia con capital deja de funcionar.

Compara el desempeño en vivo del sleeve contra la distribución del Monte Carlo del
laboratorio (propuesta, sección 3.3) y propone la degradación correspondiente:

- ``active`` → ``probation``: el drawdown en vivo rebasa el p95 del ``max_drawdown`` del
  MCReport, o el Sharpe de los últimos ``sharpe_window`` trades es negativo.
- ``probation`` → ``retired``: el drawdown rebasa el p99, o lleva ``probation_days`` días en
  observación sin recuperarse.
- ``probation`` → ``active``: NUNCA es automático; ``check`` jamás lo propone (lo decide una
  persona).

``check`` es determinista y no tiene efectos: no lee reloj ni base de datos. El instante de
evaluación viene en ``LiveStats.as_of``. Solo propone una transición por llamada y no depende
de la tabla ``strategies`` de Supabase.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from tm_agents.montecarlo import MCReport
from tm_agents.strategy_stats import StrategyState


@dataclass(frozen=True)
class TradeRecord:
    """Un trade cerrado del sleeve: retorno fraccional (0.01 = +1 %) y fecha de cierre."""

    ret: float
    closed_at: datetime | None = None


@dataclass(frozen=True)
class LiveStats:
    """Estadísticas en vivo de un sleeve.

    Attributes:
        equity: equity actual del sleeve (USDT).
        peak_equity: máximo histórico del equity desde que opera con capital.
        recent_trades: trades cerrados, del más antiguo al más reciente.
        state_entered_at: fecha de entrada al estado actual.
        as_of: instante de evaluación (con la misma zona horaria que ``state_entered_at``).
        current_drawdown: drawdown actual como fracción positiva del pico; si es ``None`` se
            calcula con ``equity`` y ``peak_equity``.
    """

    equity: float
    peak_equity: float
    recent_trades: Sequence[TradeRecord]
    state_entered_at: datetime
    as_of: datetime
    current_drawdown: float | None = None

    @property
    def drawdown(self) -> float:
        """Drawdown vigente (fracción positiva del pico)."""
        if self.current_drawdown is not None:
            return max(0.0, self.current_drawdown)
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, 1.0 - self.equity / self.peak_equity)

    @property
    def days_in_state(self) -> float:
        """Días transcurridos desde la entrada al estado actual."""
        return max(0.0, (self.as_of - self.state_entered_at).total_seconds() / 86_400)


@dataclass(frozen=True)
class LifecycleThresholds:
    """Umbrales configurables del monitor.

    Attributes:
        sharpe_window: trades que entran al Sharpe reciente.
        min_sharpe: un Sharpe reciente por debajo de este valor degrada.
        probation_days: días máximos en observación sin recuperarse.
        recovery_drawdown_fraction: en observación, se considera recuperada si el drawdown
            es menor que esta fracción del p95 del MC y el Sharpe reciente (si hay señal)
            no está por debajo de ``min_sharpe``.
    """

    sharpe_window: int = 30
    min_sharpe: float = 0.0
    probation_days: float = 30.0
    recovery_drawdown_fraction: float = 1.0

    def __post_init__(self) -> None:
        if self.sharpe_window < 2:
            raise ValueError("sharpe_window debe ser al menos 2")
        if self.probation_days < 0:
            raise ValueError("probation_days no puede ser negativo")
        if self.recovery_drawdown_fraction <= 0:
            raise ValueError("recovery_drawdown_fraction debe ser positivo")


@dataclass(frozen=True)
class Transition:
    """Cambio de estado propuesto por el monitor.

    ``from`` es palabra reservada en Python, por eso los campos se llaman ``from_state`` y
    ``to_state`` (con alias ``from_`` y ``to``).
    """

    from_state: StrategyState
    to_state: StrategyState
    reason: str
    actor: Literal["system"] = "system"

    @property
    def from_(self) -> StrategyState:
        return self.from_state

    @property
    def to(self) -> StrategyState:
        return self.to_state


def recent_sharpe(trades: Sequence[TradeRecord], window: int) -> float | None:
    """Sharpe por trade (media / desviación muestral) de los últimos ``window`` trades.

    Devuelve ``None`` (sin señal) si hay menos de ``window`` trades o algún retorno no es
    finito. Si la dispersión es nula, el signo de la media decide: ±inf o 0.
    """
    if window < 2 or len(trades) < window:
        return None
    rets = [t.ret for t in trades[-window:]]
    if not all(math.isfinite(r) for r in rets):
        return None
    mean = math.fsum(rets) / window
    var = math.fsum((r - mean) ** 2 for r in rets) / (window - 1)
    std = math.sqrt(var)
    if std < 1e-12:
        return math.copysign(math.inf, mean) if abs(mean) > 1e-12 else 0.0
    return mean / std


class LifecycleMonitor:
    """Evalúa la salud de un sleeve contra su Monte Carlo y propone degradaciones."""

    def __init__(self, thresholds: LifecycleThresholds | None = None) -> None:
        self.thresholds = thresholds or LifecycleThresholds()

    def check(
        self, strategy_state: StrategyState, live_stats: LiveStats, mc_report: MCReport
    ) -> Transition | None:
        """Devuelve la transición que corresponde o ``None`` si la estrategia sigue igual.

        Solo ``active`` y ``probation`` pueden degradarse; el resto de estados no genera
        transiciones automáticas.
        """
        if strategy_state is StrategyState.ACTIVE:
            return self._check_active(live_stats, mc_report)
        if strategy_state is StrategyState.PROBATION:
            return self._check_probation(live_stats, mc_report)
        return None

    def _check_active(self, live: LiveStats, mc: MCReport) -> Transition | None:
        th = self.thresholds
        dd = live.drawdown
        p95 = mc.max_drawdown.p95
        if dd > p95:
            return Transition(
                StrategyState.ACTIVE,
                StrategyState.PROBATION,
                f"El drawdown en vivo ({dd:.1%}) rebasa el p95 del Monte Carlo ({p95:.1%}).",
            )
        sharpe = recent_sharpe(live.recent_trades, th.sharpe_window)
        if sharpe is not None and sharpe < th.min_sharpe:
            return Transition(
                StrategyState.ACTIVE,
                StrategyState.PROBATION,
                f"El Sharpe de los últimos {th.sharpe_window} trades ({sharpe:.2f}) es menor "
                f"que el mínimo ({th.min_sharpe:.2f}); drawdown actual {dd:.1%} "
                f"(p95 del MC {p95:.1%}).",
            )
        return None

    def _check_probation(self, live: LiveStats, mc: MCReport) -> Transition | None:
        th = self.thresholds
        dd = live.drawdown
        p95, p99 = mc.max_drawdown.p95, mc.max_drawdown.p99
        if dd > p99:
            return Transition(
                StrategyState.PROBATION,
                StrategyState.RETIRED,
                f"El drawdown en vivo ({dd:.1%}) rebasa el p99 del Monte Carlo ({p99:.1%}).",
            )
        days = live.days_in_state
        if days >= th.probation_days and not self._recovered(live, p95):
            sharpe = recent_sharpe(live.recent_trades, th.sharpe_window)
            sharpe_txt = "sin señal" if sharpe is None else f"{sharpe:.2f}"
            return Transition(
                StrategyState.PROBATION,
                StrategyState.RETIRED,
                f"Lleva {days:.0f} días en observación (máximo {th.probation_days:.0f}) sin "
                f"recuperarse: drawdown {dd:.1%} (p95 del MC {p95:.1%}), Sharpe reciente "
                f"{sharpe_txt}.",
            )
        # Nunca se propone probation -> active: la reactivación es decisión humana.
        return None

    def _recovered(self, live: LiveStats, p95: float) -> bool:
        th = self.thresholds
        if live.drawdown >= p95 * th.recovery_drawdown_fraction:
            return False
        sharpe = recent_sharpe(live.recent_trades, th.sharpe_window)
        return sharpe is None or sharpe >= th.min_sharpe
