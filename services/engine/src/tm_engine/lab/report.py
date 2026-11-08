"""Salida del laboratorio: JSON (con la serie diaria de equity), Markdown y gráfica PNG."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from tm_engine.lab.walkforward import WalkForwardReport


def _clean(value: Any) -> Any:
    """Convierte NaN/inf en `None` para que el JSON sea válido."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(v) for v in value]
    return value


def _series(s: pd.Series) -> list[list[Any]]:
    return [[pd.Timestamp(t).date().isoformat(), round(float(v), 4)] for t, v in s.items()]


def to_dict(report: WalkForwardReport) -> dict[str, Any]:
    """Reporte completo serializable (por ventana, agregado, Monte Carlo y equity diario)."""
    return _clean(
        {
            "config": report.config.to_dict(),
            "aggregate": report.aggregate,
            "buy_and_hold": report.hold,
            "per_symbol": report.per_symbol,
            "windows": [
                {
                    **w.window.to_dict(),
                    "params": w.params,
                    "params_label": w.params_label,
                    "is_sharpe": w.is_sharpe,
                    "is_scores": w.is_scores,
                    "oos": w.oos,
                    "buy_and_hold": w.hold,
                    "trades": w.trades,
                    "commissions": w.commissions,
                }
                for w in report.windows
            ],
            "parameter_stability": report.stability,
            "montecarlo": report.montecarlo,
            "approval": report.approval,
            "execution": report.execution,
            "timings_seconds": report.timings,
            "equity_daily": {
                "strategy": _series(report.equity),
                "buy_and_hold": _series(report.hold_equity),
                **{f"strategy_{s}": _series(e) for s, e in report.symbol_equity.items()},
            },
        }
    )


def write_json(report: WalkForwardReport, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_dict(report), indent=2, ensure_ascii=False))
    return path


def _pct(x: float | None, digits: int = 1) -> str:
    return "—" if x is None or not math.isfinite(x) else f"{x * 100:.{digits}f} %"


def _num(x: float | None, digits: int = 2) -> str:
    return "—" if x is None or not math.isfinite(x) else f"{x:.{digits}f}"


def _duration(days: float | None) -> str:
    if days is None:
        return "—"
    return f"{days * 24:.1f} horas" if days < 1 else f"{days:.1f} días"


def _money(x: float) -> str:
    return f"{x:,.0f}"


def to_markdown(report: WalkForwardReport, image: str | None = None) -> str:
    """Resumen en Markdown con las tablas por ventana, el agregado y el veredicto."""
    cfg, agg, hold = report.config, report.aggregate, report.hold
    lines: list[str] = []
    verdict = "APROBADA" if report.approved else "RECHAZADA"
    lines += [
        f"**Veredicto: {verdict}** con los criterios de `ApprovalCriteria` "
        f"(drawdown p95 < {_pct(cfg.max_drawdown, 0)}).",
        "",
    ]
    lines += ["### Agregado out-of-sample contra comprar y mantener", ""]
    lines += [
        "| | Estrategia | Comprar y mantener |",
        "|---|---|---|",
        f"| Periodo | {agg['start']} a {agg['end']} | igual |",
        f"| Retorno total | {_pct(agg['total_return'])} | {_pct(hold['total_return'])} |",
        f"| CAGR | {_pct(agg['cagr'])} | {_pct(hold['cagr'])} |",
        f"| Sharpe (365 d) | {_num(agg['sharpe'])} | {_num(hold['sharpe'])} |",
        f"| Volatilidad anual | {_pct(agg['volatility'])} | {_pct(hold['volatility'])} |",
        f"| Drawdown máximo | {_pct(agg['max_drawdown'])} | {_pct(hold['max_drawdown'])} |",
        f"| Equity final (de {_money(cfg.capital)} USDT) | {_money(agg['final_equity'])} | "
        f"{_money(hold['final_equity'])} |",
        f"| Trades | {agg['trades']} ({_num(agg['trades_per_year'], 0)} al año) | 2 |",
        f"| Duración mediana de un trade | {_duration(agg['median_trade_days'])} | — |",
        f"| Trades ganadores | {_pct(agg['win_rate'])} | — |",
        f"| Comisiones pagadas | {_money(agg['commissions'])} USDT "
        f"({_pct(agg['commissions_pct_capital'])} del capital) | — |",
        f"| Tiempo con posición | {_pct(agg['exposure'])} | 100 % |",
        f"| Años out-of-sample | {_num(agg['oos_years'], 2)} | — |",
        "",
    ]
    if image:
        lines += [f"![Equity diario out-of-sample]({image})", ""]

    lines += ["### Por símbolo", ""]
    lines += [
        "| Símbolo | Retorno | Sharpe | Drawdown | Trades | Comisiones | "
        "Retorno B&H | Sharpe B&H | Drawdown B&H |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for sym, d in report.per_symbol.items():
        s, h = d["strategy"], d["hold"]
        lines.append(
            f"| {sym} | {_pct(s['total_return'])} | {_num(s['sharpe'])} | "
            f"{_pct(s['max_drawdown'])} | {d['trades']} | {_money(d['commissions'])} | "
            f"{_pct(h['total_return'])} | {_num(h['sharpe'])} | {_pct(h['max_drawdown'])} |"
        )
    lines.append("")

    lines += ["### Por ventana (walk-forward)", ""]
    lines += [
        "| Tramo OOS | In-sample | Parámetros elegidos | Sharpe IS | Retorno OOS | "
        "Sharpe OOS | Drawdown OOS | Trades | Comisiones | Retorno B&H |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for w in report.windows:
        win = w.window
        lines.append(
            f"| {win.label} | {win.is_start:%Y-%m} a {win.is_end - pd.Timedelta(days=1):%Y-%m} "
            f"| {w.params_label} | {_num(w.is_sharpe)} | {_pct(w.oos['total_return'])} | "
            f"{_num(w.oos['sharpe'])} | {_pct(w.oos['max_drawdown'])} | {w.trades} | "
            f"{_money(w.commissions)} | {_pct(w.hold['total_return'])} |"
        )
    beat = sum(1 for w in report.windows if w.oos["total_return"] > w.hold["total_return"])
    lines += [
        "",
        f"La estrategia le ganó en retorno a comprar y mantener en {beat} de "
        f"{len(report.windows)} tramos.",
        "",
    ]

    st = report.stability
    lines += ["### Estabilidad de los parámetros", ""]
    lines += [
        f"- {st['distinct']} combinaciones distintas en {len(report.windows)} tramos y "
        f"{st['changes']} cambios entre tramos consecutivos.",
        f"- La más frecuente (`{st['most_common']}`) se eligió en "
        f"{_pct(st['most_common_share'], 0)} de los tramos.",
        "",
        "| Parámetros | Tramos |",
        "|---|---|",
        *[f"| {k} | {v} |" for k, v in sorted(st["counts"].items(), key=lambda kv: -kv[1])],
        "",
    ]

    mc = report.montecarlo
    dd = mc["max_drawdown"]
    lines += ["### Monte Carlo del drawdown", ""]
    lines += [
        f"Block bootstrap de los {mc['horizon_days']} retornos diarios out-of-sample del sleeve "
        f"(bloques de {mc['block_size']} días, {mc['n_simulations']:,} simulaciones, semilla "
        f"{cfg.seed}). Cada simulación cubre el mismo horizonte que el backtest.",
        "",
        "| Drawdown máximo | p50 | p95 | p99 |",
        "|---|---|---|---|",
        f"| Simulado | {_pct(dd['p50'])} | {_pct(dd['p95'])} | {_pct(dd['p99'])} |",
        "",
        f"Probabilidad de ruina (perder la mitad del capital en algún punto): "
        f"{_pct(mc['probability_of_ruin'])}.",
        "",
    ]
    one = mc.get("max_drawdown_1y")
    if one:
        lines += [
            "Como referencia, con el mismo bootstrap pero en horizontes de un año (el criterio "
            f'habla de "un año malo"): p50 {_pct(one["p50"])}, p95 {_pct(one["p95"])} y p99 '
            f"{_pct(one['p99'])}. El veredicto usa el horizonte completo, que es más exigente.",
            "",
        ]

    lines += ["### Criterios de aprobación", ""]
    lines += ["| Criterio | Valor | Umbral | ¿Pasa? |", "|---|---|---|---|"]
    names = {
        "oos_sharpe": ("Sharpe out-of-sample", lambda v: _num(v), lambda t: f"> {t:.2f}"),
        "max_drawdown_p95": ("Drawdown p95", lambda v: _pct(v), lambda t: f"< {_pct(t, 0)}"),
        "min_trades": (
            "Evidencia (trades)",
            lambda v: f"{v:.0f} en {agg['oos_years']:.1f} años",
            lambda t: f"≥ {t:.0f}, o ≥ 30 con ≥ 3 años",
        ),
    }
    for c in report.approval["checks"]:
        label, fv, ft = names[c["name"]]
        lines.append(f"| {label} | {fv(c['value'])} | {ft(c['threshold'])} | "
                     f"{'sí' if c['passed'] else 'no'} |")  # fmt: skip
    beats = report.approval["beats_buy_and_hold_sharpe"]
    lines += [
        "",
        f"Sharpe contra comprar y mantener (propuesta pendiente de aprobación, no entra al "
        f"veredicto): {_num(agg['sharpe'])} contra {_num(hold['sharpe'])}, "
        f"{'le gana' if beats else 'no le gana'}.",
        "",
    ]

    ex = report.execution
    if ex.get("engine") == "nautilus":
        lines += ["### Ejecución: Nautilus contra el simulador vectorizado", ""]
        lines += [
            f"- Nautilus (`run_backtest`, comisión de {_pct(cfg.fee)} por lado): equity final "
            f"de {_money(ex['final_equity_nautilus'])} USDT con {ex['fills']} fills en "
            f"{ex['nautilus_seconds']:.1f} s.",
            f"- Simulador vectorizado: {_money(ex['final_equity_vector'])} USDT. Diferencia "
            f"relativa máxima del equity diario: {_pct(ex['max_rel_diff'], 3)}.",
            f"- Decisiones rechazadas por el motor: {ex['n_rejections']}.",
            "",
        ]
    return "\n".join(lines)


def plot_equity(report: WalkForwardReport, path: str | Path, title: str | None = None) -> Path:
    """Curva de equity diario de la estrategia contra comprar y mantener, con su drawdown."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    eq, hold = report.equity, report.hold_equity
    fig, (ax, ax_dd) = plt.subplots(
        2, 1, figsize=(10, 6), sharex=True, gridspec_kw={"height_ratios": [3, 1]}
    )
    ax.plot(eq.index, eq.to_numpy(), color="#2a6fdb", lw=1.6, label="Estrategia (OOS)")
    ax.plot(hold.index, hold.to_numpy(), color="#9aa0a6", lw=1.2, label="Comprar y mantener")
    for w in report.windows[1:]:
        ax.axvline(w.window.oos_start, color="#e0e0e0", lw=0.6, zorder=0)
    ax.set_yscale("log")
    ax.set_ylabel("Equity (USDT, escala log)")
    ax.legend(loc="upper left", frameon=False)
    ax.set_title(title or f"{report.config.strategy}: walk-forward out-of-sample")
    ax.grid(axis="y", color="#f0f0f0")
    for series, color in ((eq, "#2a6fdb"), (hold, "#9aa0a6")):
        dd = series / series.cummax() - 1.0
        ax_dd.plot(dd.index, dd.to_numpy() * 100, color=color, lw=1.0)
    ax_dd.set_ylabel("Drawdown (%)")
    ax_dd.grid(axis="y", color="#f0f0f0")
    for a in (ax, ax_dd):
        a.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path
