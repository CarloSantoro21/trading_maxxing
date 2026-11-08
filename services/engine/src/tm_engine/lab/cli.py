"""CLI del laboratorio.

    uv run --package tm-engine python -m tm_engine.lab run --strategy trend \\
        --symbols BTCUSDT ETHUSDT --data-dir data/bars --out-dir reports/lab

    uv run --package tm-engine python -m tm_engine.lab kronos \\
        --symbols BTCUSDT ETHUSDT --data-dir data/bars --forecasts-dir data/forecasts

Escribe `<out-dir>/<estrategia>.json` (con la serie diaria de equity), `<estrategia>.md` y
`<estrategia>.png`. El holdout (2025) solo se corre con `--holdout`.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from tm_engine.lab.config import WalkForwardConfig


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    p.add_argument("--data-dir", default=os.environ.get("TM_DATA_DIR", "data/bars"))
    p.add_argument("--out-dir", default="reports/lab")
    p.add_argument("--capital", type=float, default=100_000.0)
    p.add_argument("--fee", type=float, default=0.001, help="Comisión por lado (0.001 = 0.1 %%)")
    p.add_argument("--max-drawdown", type=float, default=0.20, help="Límite del drawdown p95")
    p.add_argument("--n-sims", type=int, default=10_000)
    p.add_argument("--block-size", type=int, default=20)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--engine", choices=["nautilus", "vector"], default="nautilus")
    p.add_argument(
        "--holdout",
        action="store_true",
        help="Corre sobre el holdout (2025). Solo una vez, al final del desarrollo.",
    )
    p.add_argument("--no-plot", action="store_true")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="tm_engine.lab", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Walk-forward de trend o intraday_momentum")
    run.add_argument("--strategy", required=True, choices=["trend", "intraday_momentum"])
    run.add_argument("--oos-start", default="2021-01-01")
    run.add_argument("--oos-end", default="2025-01-01", help="Exclusivo")
    run.add_argument("--is-months", type=int, default=12)
    run.add_argument("--oos-months", type=int, default=3)
    _common(run)
    kr = sub.add_parser("kronos", help="Umbrales de Kronos (jul-dic 2024) y su holdout")
    kr.add_argument("--forecasts-dir", default="data/forecasts")
    _common(kr)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    out_dir = Path(args.out_dir)
    if args.command == "kronos":
        from tm_engine.lab.kronos import KronosLabConfig, kronos_lab

        kcfg = KronosLabConfig(
            symbols=tuple(args.symbols),
            data_dir=args.data_dir,
            forecasts_dir=args.forecasts_dir,
            include_holdout=args.holdout,
            capital=args.capital,
            fee=args.fee,
            max_drawdown=args.max_drawdown,
            n_simulations=args.n_sims,
            block_size=args.block_size,
            seed=args.seed,
            engine=args.engine,
        )
        try:
            result = kronos_lab(kcfg)
        except FileNotFoundError as exc:
            print(f"faltan pronósticos precalculados: {exc}", file=sys.stderr)
            return 2
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "kronos.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
        print(json.dumps({k: result[k] for k in ("chosen", "in_sample")}, indent=2))
        return 0

    from tm_engine.lab.report import plot_equity, to_markdown, write_json
    from tm_engine.lab.walkforward import walk_forward

    config = WalkForwardConfig(
        strategy=args.strategy,
        symbols=tuple(args.symbols),
        oos_start=args.oos_start,
        oos_end=args.oos_end,
        is_months=args.is_months,
        oos_months=args.oos_months,
        capital=args.capital,
        fee=args.fee,
        engine=args.engine,
        n_simulations=args.n_sims,
        block_size=args.block_size,
        seed=args.seed,
        max_drawdown=args.max_drawdown,
        data_dir=args.data_dir,
    )
    if args.holdout:
        config = config.holdout()
    report = walk_forward(config)
    name = args.strategy + ("-holdout" if args.holdout else "")
    write_json(report, out_dir / f"{name}.json")
    image = None
    if not args.no_plot:
        image = plot_equity(report, out_dir / f"{name}.png").name
    (out_dir / f"{name}.md").write_text(to_markdown(report, image=image))
    agg = report.aggregate
    print(
        json.dumps(
            {
                "strategy": args.strategy,
                "approved": report.approved,
                "reasons": report.approval["reasons"],
                "sharpe": agg["sharpe"],
                "total_return": agg["total_return"],
                "max_drawdown": agg["max_drawdown"],
                "trades": agg["trades"],
                "buy_and_hold_sharpe": report.hold["sharpe"],
                "out_dir": str(out_dir),
            },
            indent=2,
        )
    )
    return 0


__all__ = ["main", "parse_args"]
