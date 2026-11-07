"""Contabilidad por sleeve: cada estrategia lleva su posición, su costo y su P&L.

Nautilus agrupa las posiciones por instrumento (OMS netting), así que dos sleeves que compran
BTC terminan en la misma posición del venue. Este libro lleva la cuenta aparte: cada fill trae
la etiqueta de su sleeve (`sleeve:<nombre>` en los tags de la orden) y aquí se acumula con
costo promedio. En spot solo hay compras que abren y ventas que cierran.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from tm_shared.contracts import OrderSide

SLEEVE_TAG_PREFIX = "sleeve:"
DEFAULT_SLEEVE = "default"
ZERO = Decimal("0")


def sleeve_tag(sleeve: str) -> str:
    return f"{SLEEVE_TAG_PREFIX}{sleeve}"


def sleeve_from_tags(tags: list[str] | None) -> str:
    """Extrae el sleeve de los tags de una orden; `default` si no trae ninguno."""
    for tag in tags or []:
        if tag.startswith(SLEEVE_TAG_PREFIX):
            return tag[len(SLEEVE_TAG_PREFIX) :]
    return DEFAULT_SLEEVE


@dataclass
class SleevePosition:
    """Posición de un sleeve en un símbolo."""

    quantity: Decimal = ZERO
    avg_price: Decimal = ZERO


@dataclass
class SleeveStats:
    """Resultado acumulado de un sleeve (todas sus posiciones)."""

    name: str
    positions: dict[str, SleevePosition] = field(default_factory=dict)
    realized_pnl: Decimal = ZERO  # en moneda de cotización (USDT), ya sin comisiones
    commissions: Decimal = ZERO
    fills: int = 0
    closed_trades: int = 0

    def quantity(self, symbol: str) -> Decimal:
        pos = self.positions.get(symbol)
        return pos.quantity if pos else ZERO

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "realized_pnl": str(self.realized_pnl),
            "commissions": str(self.commissions),
            "fills": self.fills,
            "closed_trades": self.closed_trades,
            "positions": {
                s: {"quantity": str(p.quantity), "avg_price": str(p.avg_price)}
                for s, p in self.positions.items()
                if p.quantity > 0
            },
        }


class SleeveLedger:
    """Libro de sleeves alimentado por los fills de la estrategia."""

    def __init__(self) -> None:
        self._sleeves: dict[str, SleeveStats] = {}

    def get(self, sleeve: str) -> SleeveStats:
        if sleeve not in self._sleeves:
            self._sleeves[sleeve] = SleeveStats(name=sleeve)
        return self._sleeves[sleeve]

    @property
    def sleeves(self) -> dict[str, SleeveStats]:
        return dict(self._sleeves)

    def quantity(self, sleeve: str, symbol: str) -> Decimal:
        return self._sleeves[sleeve].quantity(symbol) if sleeve in self._sleeves else ZERO

    def apply_fill(
        self,
        *,
        sleeve: str,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        price: Decimal,
        commission: Decimal = ZERO,
    ) -> Decimal:
        """Registra un fill y devuelve el P&L realizado por ese fill (0 en las compras).

        La comisión (en USDT) se descuenta del P&L realizado del sleeve en el momento del fill.
        Una venta mayor que la posición del sleeve se recorta a lo que tiene: en spot el
        sleeve no puede quedar en corto.
        """
        stats = self.get(sleeve)
        pos = stats.positions.setdefault(symbol, SleevePosition())
        stats.fills += 1
        stats.commissions += commission
        realized = -commission
        if side is OrderSide.BUY:
            total = pos.quantity + quantity
            pos.avg_price = (pos.avg_price * pos.quantity + price * quantity) / total
            pos.quantity = total
        else:
            closed = min(quantity, pos.quantity)
            realized += (price - pos.avg_price) * closed
            pos.quantity -= closed
            if pos.quantity == 0:
                pos.avg_price = ZERO
                stats.closed_trades += 1
        stats.realized_pnl += realized
        return realized

    def to_dict(self) -> dict[str, dict[str, object]]:
        return {name: s.to_dict() for name, s in sorted(self._sleeves.items())}
