# agents

Worker con la biblioteca de estrategias (KronosAgent, TrendAgent, IntradayMomentumAgent), NewsAgent, AgentOrchestrator, PortfolioAllocator, SimulationGate, RiskAgent y MonteCarloService.

```bash
uv run --package tm-agents python -m tm_agents
```

## MonteCarloService (laboratorio)

`tm_agents.montecarlo` remuestrea los retornos por trade de un backtest y aplica los criterios de aprobación (propuesta, sección 3.2, fase 1).

```python
from tm_agents.montecarlo import ApprovalCriteria, bootstrap, evaluate, reshuffle

report = reshuffle(trades, 10_000, seed=42, initial_capital=10_000, ruin_level=0.7)
report.max_drawdown.p95  # drawdown máximo del percentil 95
report.probability_of_ruin  # P(equity < 70 % del capital en algún punto del camino)
report.equity_fan.p50  # mediana del equity por paso, para el abanico

report_bb = bootstrap(trades, 10_000, seed=42, block_size=10)  # block bootstrap

result = evaluate(
    report, oos_sharpe=0.8, n_trades=len(trades), criteria=ApprovalCriteria(max_drawdown_p95=0.25)
)
result.approved, result.reasons
```

`reshuffle` permuta sin reemplazo (conserva el retorno final y cambia el camino); `bootstrap` remuestrea con reemplazo, por bloques circulares si `block_size > 1`. La semilla es obligatoria.

Para tomar el límite de drawdown del perfil: `ApprovalCriteria.for_profile(profile.max_drawdown)` (el drawdown p95 debe quedar por debajo del `max_drawdown` del usuario).

## PortfolioAllocator (capital por sleeve)

`tm_agents.allocator.PortfolioAllocator` reparte el capital del perfil entre los sleeves de las estrategias a partir de sus `StrategyStats` (`tm_agents.strategy_stats`).

```python
from decimal import Decimal
from tm_shared.contracts import Horizon
from tm_agents.allocator import PortfolioAllocator
from tm_agents.strategy_stats import StrategyState, StrategyStats

stats = [
    StrategyStats(
        "trend", StrategyState.ACTIVE, volatility=0.30, oos_sharpe=0.9, horizon=Horizon.LONG
    ),
    StrategyStats("kronos", StrategyState.PROBATION, volatility=0.45, oos_sharpe=0.7),
    StrategyStats("momentum", StrategyState.SHADOW, volatility=0.25, oos_sharpe=1.1),
]
allocator = (
    PortfolioAllocator()
)  # pesos por horizonte, penalización y nocional mínimo configurables
allocator.allocate(Decimal("1000"), Horizon.MEDIUM, stats, max_drawdown=0.20)
res = allocator.allocate_detailed(Decimal("1000"), Horizon.MEDIUM, stats)
res.sleeves, res.unallocated, res.weights, res.excluded
```

- Peso = peso del horizonte / volatilidad validada / (1 + k · correlación positiva promedio con las demás). La correlación sale de la matriz opcional (`correlation=`) o, si falta, de `StrategyStats.returns`.
- `active` recibe su peso completo, `probation` la mitad (lo liberado pasa a las activas; si no hay activas queda en `unallocated`), y `research`, `lab`, `shadow` y `retired` reciben 0. También quedan en 0 las de volatilidad no positiva, Sharpe OOS ≤ 0 o drawdown p95 ≥ `max_drawdown` del perfil.
- Un sleeve por debajo del nocional mínimo (10 USDT) se elimina y su capital se redistribuye. Los montos se redondean hacia abajo a 0.01 USDT y los centavos sobrantes se reparten por residuos mayores, así que `sum(sleeves) + unallocated == capital`.

## SimulationGate (antes de cada operación)

`tm_agents.simulation_gate` estima, para una decisión con SL y TP, P(TP primero), P(SL primero), P(abierta al final del horizonte) y el valor esperado en R, y bloquea si es ≤ 0.

```python
from tm_agents.simulation_gate import GateConfig, SimulationGate

gate = SimulationGate(GateConfig(n_paths=10_000, lookback_days=60, seed=42, external_weight=0.3))
bars = market_data.get_bars("BTCUSDT", "15m", start=now - timedelta(days=60))
paths = gate.build_paths(bars, entry=60_000.0, horizon=96)  # solo bootstrap
paths = gate.build_paths(bars, entry=60_000.0, horizon=96, external=forecast.paths)  # + Kronos
decision, verdict = gate.evaluate(decision, paths)  # decision.rationale.sim queda lleno
verdict.approved, verdict.reasons
```

- **Bootstrap histórico**: cada paso futuro remuestrea (con reemplazo y semilla fija) una vela real de la misma hora del día (UTC) dentro de los últimos `lookback_days`, aplicando sus factores `high/low/close` relativos al cierre anterior. Si una hora no tiene velas, se usa toda la ventana.
- **Trayectorias externas**: `(samples, pasos)` de cierres o `(samples, pasos, OHLC[V])` como `Forecast.paths` de Kronos. `external_weight` es el peso total de esa fuente, sin importar cuántas trayectorias traiga.
- **Regla conservadora**: si en la misma vela se tocan SL y TP, cuenta como SL.
- `fee_rate` (comisión por lado) se descuenta del resultado en R. 10,000 trayectorias de 96 pasos tardan decenas de milisegundos.

## KronosAgent (`strategies/kronos.py`, registrado como `kronos`)

Usa velas de 4 h: las toma del contexto (`Timeframe.H4`) o las remuestrea de 15m, 30m o 1m
con `tm_marketdata.resample_ohlcv` (solo velas completas, sin look-ahead). En cada cierre pide
`n_trajectories` trayectorias (default 20) de `pred_len` velas (default 6) a
`POST {FORECAST_URL}/predict` (default `http://localhost:8001`, timeout `FORECAST_TIMEOUT`
de 120 s).

- **Dirección**: mediana de los retornos finales de las trayectorias. En spot: `long` si es
  mayor que `min_return`, si no `flat` (nunca cortos, salvo `allow_short=True`).
- **Confianza**: fracción de trayectorias que coinciden con la dirección.
- **metadata**: mediana, media, percentiles 5 y 95, `p_up` y la probabilidad de tocar primero
  TP o SL con `stop_loss_pct` y `take_profit_pct`.
- **`trajectories(ctx)`**: arreglo `(n, pred_len, 5)` OHLCV para
  `PathSet.from_array` de la SimulationGate. El pronóstico del mismo `(symbol, now)` se pide
  una sola vez.
- Si forecast no responde (timeout, error HTTP o respuesta inválida) o no hay 32 velas de
  4 h, la señal es `flat` con confianza 0 y el error en `metadata["error"]`.
