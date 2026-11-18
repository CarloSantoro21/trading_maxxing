#!/usr/bin/env python3
"""Mide P&L, Sharpe, drawdown y nº de operaciones de la ventana de paper trading (#44).

Entrada preferida: un CSV de fills exportado desde `public.trades` con las columnas
`ts,symbol,side,quantity,entry_price,exit_price,fees,pnl`. Es lo que genera el SQL del
checklist `docs/validacion-checklist.md`.

El modelo de comisiones asumido es 0.1 % por lado (Binance Spot). Si la fila ya trae `fees`,
se usa tal cual; si no, se estima con el notional de entrada + salida. El P&L neto siempre
descuenta `fees`, ya sea leído o estimado.

Salida: un JSON machine-readable (por defecto) o un fragmento markdown listo para pegar en
`docs/validacion.md` cuando se pasa `--format md`.

Este script es solo lectura. No conecta con Binance ni muta Supabase.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

DEFAULT_FEE_PER_SIDE = Decimal("0.001")  # 0.1 % Binance Spot
TRADING_DAYS_PER_YEAR = 365  # cripto opera todos los días


@dataclass(frozen=True)
class Fill:
    """Operación cerrada leída del CSV."""

    ts: datetime
    symbol: str
    side: str
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    fees: Decimal
    pnl_reported: Decimal | None

    @property
    def gross_pnl(self) -> Decimal:
        """P&L bruto antes de comisiones, asumiendo lado largo (spot)."""
        return (self.exit_price - self.entry_price) * self.quantity

    def net_pnl(self, fee_per_side: Decimal) -> Decimal:
        """P&L neto de comisiones. Usa el `pnl` reportado si viene; si no, lo recalcula."""
        if self.pnl_reported is not None:
            return self.pnl_reported
        fees = self.fees if self.fees > 0 else self._estimated_fees(fee_per_side)
        return self.gross_pnl - fees

    def _estimated_fees(self, fee_per_side: Decimal) -> Decimal:
        notional_in = self.entry_price * self.quantity
        notional_out = self.exit_price * self.quantity
        return (notional_in + notional_out) * fee_per_side


@dataclass
class Metrics:
    """Resumen de métricas para la decisión go/no-go."""

    start: datetime | None
    end: datetime | None
    days_effective: int
    trades: int
    winners: int
    hit_rate: float
    total_pnl: Decimal
    total_fees: Decimal
    gross_return_pct: float | None
    sharpe_daily: float | None
    volatility_annualized: float | None
    max_drawdown_pct: float
    daily_returns: list[tuple[date, float]] = field(default_factory=list)

    def to_json_dict(self, capital: Decimal | None) -> dict[str, Any]:
        return {
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "days_effective": self.days_effective,
            "trades": self.trades,
            "winners": self.winners,
            "hit_rate": round(self.hit_rate, 4),
            "total_pnl": str(self.total_pnl),
            "total_fees": str(self.total_fees),
            "capital_initial": str(capital) if capital is not None else None,
            "gross_return_pct": (
                round(self.gross_return_pct, 4) if self.gross_return_pct is not None else None
            ),
            "sharpe_daily": (
                round(self.sharpe_daily, 4) if self.sharpe_daily is not None else None
            ),
            "volatility_annualized": (
                round(self.volatility_annualized, 4)
                if self.volatility_annualized is not None
                else None
            ),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "daily_returns": [
                {"date": d.isoformat(), "return": round(r, 6)} for d, r in self.daily_returns
            ],
        }


def _parse_ts(raw: str) -> datetime:
    value = raw.strip()
    if not value:
        raise ValueError("timestamp vacío")
    # Acepta ISO 8601 con o sin zona.
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_decimal(raw: str | None, *, default: Decimal | None = None) -> Decimal | None:
    if raw is None:
        return default
    value = raw.strip()
    if not value:
        return default
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"no se pudo parsear '{raw}' como Decimal") from exc


def load_fills(path: Path) -> list[Fill]:
    """Lee el CSV exportado desde `public.trades` y devuelve solo operaciones cerradas."""
    fills: list[Fill] = []
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"ts", "symbol", "side", "quantity", "entry_price", "exit_price"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"faltan columnas en el CSV: {sorted(missing)}")
        for idx, row in enumerate(reader, start=2):  # línea 1 es el header
            exit_price = _parse_decimal(row.get("exit_price"))
            if exit_price is None:
                # Operación todavía abierta: no entra al cálculo.
                continue
            try:
                quantity = _parse_decimal(row["quantity"])
                entry_price = _parse_decimal(row["entry_price"])
                assert quantity is not None and entry_price is not None
                fills.append(
                    Fill(
                        ts=_parse_ts(row["ts"]),
                        symbol=row["symbol"].strip(),
                        side=row["side"].strip().lower(),
                        quantity=quantity,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        fees=_parse_decimal(row.get("fees"), default=Decimal("0")) or Decimal("0"),
                        pnl_reported=_parse_decimal(row.get("pnl")),
                    )
                )
            except (KeyError, ValueError, AssertionError) as exc:
                raise ValueError(f"fila {idx} inválida: {exc}") from exc
    fills.sort(key=lambda f: f.ts)
    return fills


def _mean(values: Iterable[float]) -> float:
    data = list(values)
    return sum(data) / len(data) if data else 0.0


def _stdev(values: Iterable[float]) -> float:
    data = list(values)
    if len(data) < 2:
        return 0.0
    m = _mean(data)
    variance = sum((x - m) ** 2 for x in data) / (len(data) - 1)
    return math.sqrt(variance)


def _daily_returns(
    fills: list[Fill], fee_per_side: Decimal, capital: Decimal | None
) -> list[tuple[date, float]]:
    """Agrega el P&L neto por día (UTC) y lo expresa como retorno sobre `capital` si se da.

    Si no hay capital, usa la fracción respecto al equity acumulado al inicio del día
    (capital inicial implícito = max(|pnl acumulado|, 1) para evitar divisiones por cero).
    """
    if not fills:
        return []
    buckets: dict[date, Decimal] = {}
    for fill in fills:
        day = fill.ts.date()
        buckets[day] = buckets.get(day, Decimal("0")) + fill.net_pnl(fee_per_side)
    ordered = sorted(buckets.items())
    base = capital if capital is not None and capital > 0 else Decimal("1")
    equity = base
    out: list[tuple[date, float]] = []
    for day, pnl in ordered:
        ret = float(pnl / equity) if equity > 0 else 0.0
        out.append((day, ret))
        equity += pnl
    return out


def _max_drawdown(fills: list[Fill], fee_per_side: Decimal, capital: Decimal | None) -> float:
    """Drawdown máximo sobre la curva de equity reconstruida con el P&L neto por operación."""
    if not fills:
        return 0.0
    base = capital if capital is not None and capital > 0 else Decimal("0")
    equity = base
    peak = base
    worst = 0.0
    saw_peak = False
    for fill in fills:
        equity += fill.net_pnl(fee_per_side)
        if not saw_peak or equity > peak:
            peak = equity
            saw_peak = True
        if peak > 0:
            dd = float((peak - equity) / peak)
            if dd > worst:
                worst = dd
    return worst


def compute_metrics(
    fills: list[Fill],
    *,
    fee_per_side: Decimal = DEFAULT_FEE_PER_SIDE,
    capital: Decimal | None = None,
) -> Metrics:
    """Calcula el bloque de métricas a partir de la lista de fills cerrados."""
    if not fills:
        return Metrics(
            start=None,
            end=None,
            days_effective=0,
            trades=0,
            winners=0,
            hit_rate=0.0,
            total_pnl=Decimal("0"),
            total_fees=Decimal("0"),
            gross_return_pct=None,
            sharpe_daily=None,
            volatility_annualized=None,
            max_drawdown_pct=0.0,
        )

    start = fills[0].ts
    end = fills[-1].ts
    total_pnl = sum((f.net_pnl(fee_per_side) for f in fills), Decimal("0"))
    total_fees = sum(
        (
            f.fees if f.fees > 0 else (f.entry_price + f.exit_price) * f.quantity * fee_per_side
            for f in fills
        ),
        Decimal("0"),
    )
    winners = sum(1 for f in fills if f.net_pnl(fee_per_side) > 0)
    hit_rate = winners / len(fills)
    daily = _daily_returns(fills, fee_per_side, capital)
    days_effective = len({f.ts.date() for f in fills})

    returns_only = [r for _, r in daily]
    sharpe: float | None = None
    vol_annual: float | None = None
    if len(returns_only) >= 2:
        mu = _mean(returns_only)
        sigma = _stdev(returns_only)
        if sigma > 0:
            sharpe = (mu / sigma) * math.sqrt(TRADING_DAYS_PER_YEAR)
            vol_annual = sigma * math.sqrt(TRADING_DAYS_PER_YEAR)

    gross_return_pct: float | None = None
    if capital is not None and capital > 0:
        gross_return_pct = float(total_pnl / capital)

    return Metrics(
        start=start,
        end=end,
        days_effective=days_effective,
        trades=len(fills),
        winners=winners,
        hit_rate=hit_rate,
        total_pnl=total_pnl,
        total_fees=total_fees,
        gross_return_pct=gross_return_pct,
        sharpe_daily=sharpe,
        volatility_annualized=vol_annual,
        max_drawdown_pct=_max_drawdown(fills, fee_per_side, capital),
        daily_returns=daily,
    )


def render_markdown(metrics: Metrics, *, capital: Decimal | None) -> str:
    """Fragmento listo para pegar en la sección 5 de `docs/validacion.md`."""

    def fmt_opt(value: float | None, suffix: str = "") -> str:
        return f"{value:.4f}{suffix}" if value is not None else "TBD"

    lines = [
        "| Métrica | Valor |",
        "|---|---|",
        f"| Periodo medido | {metrics.start.isoformat() if metrics.start else 'TBD'} → "
        f"{metrics.end.isoformat() if metrics.end else 'TBD'} |",
        f"| Días con operaciones | {metrics.days_effective} |",
        f"| Operaciones cerradas | {metrics.trades} |",
        f"| Hit rate | {metrics.hit_rate:.2%} |",
        f"| P&L neto (USDT) | {metrics.total_pnl:.4f} |",
        f"| Comisiones totales (USDT) | {metrics.total_fees:.4f} |",
        f"| Retorno neto sobre capital | {fmt_opt(metrics.gross_return_pct)} |",
        f"| Sharpe (diario, anualizado) | {fmt_opt(metrics.sharpe_daily)} |",
        f"| Volatilidad anualizada | {fmt_opt(metrics.volatility_annualized)} |",
        f"| Drawdown máximo observado | {metrics.max_drawdown_pct:.4f} |",
    ]
    if capital is not None:
        lines.insert(2, f"| Capital inicial asumido | {capital} USDT |")
    return "\n".join(lines) + "\n"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Calcula P&L, Sharpe, drawdown y nº de operaciones de la ventana de paper trading "
            "para el reporte go/no-go (#44)."
        )
    )
    parser.add_argument(
        "--fills",
        type=Path,
        required=True,
        help="CSV con las operaciones cerradas (export de public.trades).",
    )
    parser.add_argument(
        "--capital",
        type=str,
        default=None,
        help="Capital inicial virtual en USDT (opcional; habilita retorno y drawdown normalizado).",
    )
    parser.add_argument(
        "--fee",
        type=str,
        default=str(DEFAULT_FEE_PER_SIDE),
        help="Comisión por lado (por defecto 0.001 = 0.1 %%).",
    )
    parser.add_argument(
        "--format",
        choices=("json", "md"),
        default="json",
        help="Formato de salida: json (default) o fragmento markdown.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        fee = Decimal(args.fee)
    except InvalidOperation:
        print(f"--fee inválido: {args.fee}", file=sys.stderr)
        return 2
    capital: Decimal | None = None
    if args.capital is not None:
        try:
            capital = Decimal(args.capital)
        except InvalidOperation:
            print(f"--capital inválido: {args.capital}", file=sys.stderr)
            return 2

    if not args.fills.exists():
        print(f"no existe el archivo {args.fills}", file=sys.stderr)
        return 1

    fills = load_fills(args.fills)
    metrics = compute_metrics(fills, fee_per_side=fee, capital=capital)

    if args.format == "json":
        print(json.dumps(metrics.to_json_dict(capital), indent=2, sort_keys=True))
    else:
        print(render_markdown(metrics, capital=capital))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
