"""Helper de backtest: BacktestEngine con un venue simulado de Binance Spot.

Es la base del futuro `BacktestService` (#13). Arma el motor con una cuenta CASH en USDT,
OMS netting y comisión de 0.1 % por lado, carga velas de un DataFrame de `tm_marketdata`
(`MarketDataService.get_bars`) y corre `AgenticStrategy` con la fuente de decisiones que se
le inyecte (por ejemplo `ScheduledDecisionSource`).

    result = run_backtest({"BTCUSDT": frame}, ScheduledDecisionSource(envelopes))
    result.sleeves["trend"]["realized_pnl"]
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pandas as pd
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.objects import Money
from tm_shared.contracts import Timeframe

from tm_engine.decisions import DecisionSource
from tm_engine.instruments import BINANCE, SPOT_FEE, bar_type, bars_from_frame, spot_instrument
from tm_engine.recorder import FillRecord, MemoryFillRecorder
from tm_engine.strategy import AgenticStrategy, AgenticStrategyConfig


@dataclass
class BacktestResult:
    """Lo que deja una corrida: fills por sleeve, rechazos y balance final del venue."""

    fills: list[FillRecord]
    sleeves: dict[str, dict[str, Any]]
    rejections: list[str]
    orders: list[Any] = field(default_factory=list)
    final_balance_usdt: Decimal = Decimal("0")


def build_engine(
    frames: Mapping[str, pd.DataFrame],
    *,
    timeframe: Timeframe | str = Timeframe.M1,
    starting_usdt: Decimal | int = 100_000,
    fee: Decimal = SPOT_FEE,
    log_level: str = "ERROR",
) -> BacktestEngine:
    """BacktestEngine con el venue BINANCE (spot, cuenta CASH) y las velas cargadas."""
    engine = BacktestEngine(
        BacktestEngineConfig(
            trader_id=TraderId("BACKTESTER-001"),
            logging=LoggingConfig(log_level=log_level, bypass_logging=log_level == "OFF"),
        )
    )
    engine.add_venue(
        venue=BINANCE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,  # multimoneda: USDT y la base de cada par
        starting_balances=[Money(Decimal(starting_usdt), USDT)],
        bar_execution=True,
    )
    for symbol, frame in frames.items():
        instrument = spot_instrument(symbol, fee=fee)
        engine.add_instrument(instrument)
        engine.add_data(bars_from_frame(frame, instrument, timeframe))
    return engine


def run_backtest(
    frames: Mapping[str, pd.DataFrame],
    source: DecisionSource,
    *,
    timeframe: Timeframe | str = Timeframe.M1,
    starting_usdt: Decimal | int = 100_000,
    fee: Decimal = SPOT_FEE,
    log_level: str = "ERROR",
    sleeve_guard: Callable[[str], bool] | None = None,
) -> BacktestResult:
    """Corre AgenticStrategy sobre las velas dadas y devuelve el resultado por sleeve."""
    engine = build_engine(
        frames, timeframe=timeframe, starting_usdt=starting_usdt, fee=fee, log_level=log_level
    )
    recorder = MemoryFillRecorder()
    strategy = AgenticStrategy(
        AgenticStrategyConfig(bar_types=tuple(str(bar_type(s, timeframe)) for s in frames)),
        source=source,
        recorder=recorder,
        sleeve_guard=sleeve_guard,
    )
    engine.add_strategy(strategy)
    try:
        engine.run()
        account = engine.cache.account_for_venue(BINANCE)
        balance = account.balance_total(USDT) if account is not None else None
        return BacktestResult(
            fills=list(recorder.fills),
            sleeves=strategy.sleeve_report(),
            rejections=[r.reason for r in strategy.rejections],
            orders=list(engine.cache.orders()),
            final_balance_usdt=balance.as_decimal() if balance is not None else Decimal("0"),
        )
    finally:
        engine.dispose()
