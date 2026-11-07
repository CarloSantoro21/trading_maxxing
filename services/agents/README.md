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
