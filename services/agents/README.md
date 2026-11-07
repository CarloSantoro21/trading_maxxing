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

## LLMClient

`tm_agents.llm` expone `LLMClient.complete_json(prompt, schema)`, que devuelve una instancia Pydantic ya validada o lanza `LLMError`. El proveedor se elige con `LLM_PROVIDER`:

| `LLM_PROVIDER` | Implementación | Variables |
|---|---|---|
| `groq` (por defecto) | `GroqClient` (API compatible con OpenAI, `response_format=json_schema`) | `GROQ_API_KEY`, `GROQ_MODEL` (`openai/gpt-oss-20b`), `LLM_TIMEOUT_SECONDS` (20), `LLM_MAX_RETRIES` (3) |
| `fake` | `FakeLLM` (respuestas predefinidas, para pruebas) | — |

`GroqClient` reintenta con backoff exponencial (respeta `Retry-After`) ante timeouts, errores de red, 408/409/429/5xx, JSON inválido o fuera del esquema; los demás 4xx fallan de inmediato.

```python
from tm_agents.llm import build_llm_client

llm = build_llm_client()  # lee LLM_PROVIDER y GROQ_API_KEY del entorno
result = llm.complete_json("...", MiEsquema)
```

## NewsAgent

`tm_agents.news` baja titulares de CryptoPanic (API v2, `CRYPTOPANIC_TOKEN`), los clasifica con el `LLMClient` (impacto `low`/`high`/`critical`, activos afectados y sentimiento en [-1, 1]) y **solo veta nuevas entradas**; nunca abre ni cierra operaciones.

| Impacto | Alcance | Duración (desde la publicación) |
|---|---|---|
| `low` | ninguno | — |
| `high` | activo afectado (`BTC` bloquea `BTCUSDT`) | 4 h (`NEWS_VETO_HIGH_HOURS`) |
| `critical` | todo el mercado (`*`) | 24 h (`NEWS_VETO_CRITICAL_HOURS`) |

Los vetos se guardan en Redis (`REDIS_URL`, claves `tm:news:veto:<scope>` con TTL) y, si Redis no está o falla, en memoria del proceso.

**Fail-open:** si CryptoPanic o el LLM fallan, el ciclo no veta, registra un warning y lo reintenta en el siguiente ciclo (`RefreshResult.failed_open`). El RiskAgent y los SL siguen protegiendo el capital.

```python
from tm_agents.news import news_agent_from_env

agent = news_agent_from_env()
agent.refresh(["BTCUSDT", "ETHUSDT"])  # noticias -> clasificación -> vetos
agent.active_veto("BTCUSDT")  # Veto vigente o None
```

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
