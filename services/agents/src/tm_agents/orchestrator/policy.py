"""Niveles de cada operación por estrategia: stop-loss, take-profit y horizonte del gate.

Las estrategias de la biblioteca emiten una dirección y una confianza, no niveles. Hay dos
tipos de salida:

- **Por señal** (Trend, Intraday Momentum): la estrategia decide cuándo salir; el bracket solo
  lleva un SL y un TP **de catástrofe** muy lejos (5 % y 8 veces el precio), igual que en el
  laboratorio (`tm_engine.lab.execution`), para que no se disparen en condiciones normales.
- **Con stop real** (Kronos): los niveles salen de la señal (`metadata.stop_loss` y
  `metadata.take_profit`, con los porcentajes del agente). Con stop real el RiskAgent también
  recorta el tamaño por riesgo por operación.

`gate_horizon` es cuántas velas del timeframe de la estrategia simula la SimulationGate.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from tm_shared.contracts import Signal

CATASTROPHE_STOP = Decimal("0.05")
CATASTROPHE_TAKE = Decimal("8")
MAX_PRICE = Decimal("999999")
PRICE_STEP = Decimal("0.01")


@dataclass(frozen=True)
class SleevePolicy:
    """Cómo se arman los niveles y la simulación de una estrategia."""

    gate_horizon: int = 10
    signal_levels: bool = False  # True: SL/TP de la metadata de la señal (stop real)
    use_external_paths: bool = False  # True: combina las trayectorias del agente (Kronos)
    use_gate: bool = True

    def levels(self, signal: Signal, price: Decimal) -> tuple[Decimal, Decimal, bool]:
        """``(stop_loss, take_profit, hard_stop)`` de una compra a ``price``."""
        if self.signal_levels:
            sl = _decimal(signal.metadata.get("stop_loss"))
            tp = _decimal(signal.metadata.get("take_profit"))
            if sl is not None and tp is not None and 0 < sl < price < tp:
                return _q(sl), _q(tp), True
        sl = max(PRICE_STEP, _q(price * CATASTROPHE_STOP))
        tp = min(MAX_PRICE, _q(price * CATASTROPHE_TAKE))
        return sl, tp, False


DEFAULT_POLICY = SleevePolicy()

#: Política por estrategia (las que no estén usan `DEFAULT_POLICY`).
POLICIES: Mapping[str, SleevePolicy] = MappingProxyType(
    {
        # 20 velas diarias: un poco más que la duración mediana de un trade (16 días).
        "trend": SleevePolicy(gate_horizon=20),
        # La posición dura una vela de 30 min.
        "intraday_momentum": SleevePolicy(gate_horizon=1),
        # El pronóstico de Kronos cubre `pred_len` (6) velas de 4 h.
        "kronos": SleevePolicy(gate_horizon=6, signal_levels=True, use_external_paths=True),
    }
)


def policy_for(name: str, overrides: Mapping[str, SleevePolicy] | None = None) -> SleevePolicy:
    if overrides and name in overrides:
        return overrides[name]
    return POLICIES.get(name, DEFAULT_POLICY)


def sell_levels(price: Decimal) -> tuple[Decimal, Decimal]:
    """``(stop_loss, take_profit)`` de relleno para una venta (el contrato pide TP < precio < SL).

    En spot una venta solo reduce o cierra; el motor no usa estos niveles.
    """
    return min(MAX_PRICE, _q(price * 2)), max(PRICE_STEP, _q(price / 2))


def _q(value: Decimal) -> Decimal:
    return value.quantize(PRICE_STEP)


def _decimal(value: object) -> Decimal | None:
    try:
        out = Decimal(str(value))
    except (ArithmeticError, ValueError, TypeError):
        return None
    return out if out.is_finite() else None


__all__ = ["DEFAULT_POLICY", "POLICIES", "SleevePolicy", "policy_for", "sell_levels"]
