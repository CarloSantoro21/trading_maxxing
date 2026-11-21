# agents

Worker con la biblioteca de estrategias (KronosAgent, TrendAgent, IntradayMomentumAgent), NewsAgent, AgentOrchestrator, PortfolioAllocator, SimulationGate, RiskAgent y MonteCarloService.

```bash
uv run --package tm-agents python -m tm_agents
```

El worker corre en el mismo proceso la capa compartida del orquestador (publica `tm:signals`), los BotRunner (leen `tm:signals`, publican `tm:decisions`) y el `ReferenceService` (lee `tm:signals` con su propio cursor). Cada pieza se apaga con `ORCHESTRATOR_SHARED=0`, `ORCHESTRATOR_BOTS=0` o `REFERENCE_SERVICE=0`; todas necesitan `REDIS_URL` y el `ReferenceService` además `SUPABASE_URL` y `SUPABASE_SERVICE_ROLE_KEY`. Sin `REDIS_URL` solo escribe el heartbeat. Ver `.env.example` y `docs/arquitectura/orquestador.md`.

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

## LLM y noticias: opcionales y apagados por defecto (#118)

**Por defecto no cuesta nada.** Con la configuración por defecto el motor corre completo sin ninguna llave de LLM ni de noticias: no se arma el NewsAgent, no hay llamadas al LLM, la capa compartida recibe `news=None` (sin filtro de noticias) y el ExplainerAgent usa la plantilla determinista. Todo se prende por variables de entorno y, si se quiere, en caliente desde el admin.

| Variable | Default | Valores | Qué hace |
|---|---|---|---|
| `NEWS_ENABLED` | `false` | `true`/`false` | Prende el NewsAgent (titulares → clasificación → vetos). Apagado: `news=None`. |
| `EXPLAINER_ENABLED` | `false` | `true`/`false` | Prende el ExplainerAgent con LLM. Apagado (o sin LLM): plantilla. |
| `LLM_PROVIDER` | `none` | `none`, `workers-ai`, `groq`, `openai-compatible`, `fake` | Proveedor del LLM. `none`: noticias por palabras clave y explainer con plantilla. |
| `CF_ACCOUNT_ID`, `CF_AI_API_TOKEN` | — | | Workers AI por REST (10,000 neurons al día gratis). |
| `WORKERS_AI_MODEL` | `@cf/meta/llama-3.1-8b-instruct` | | Modelo de Workers AI (debe soportar el modo JSON). |
| `GROQ_API_KEY`, `GROQ_MODEL` | —, `openai/gpt-oss-20b` | | Groq (tier gratuito). |
| `OPENAI_COMPAT_BASE_URL`, `OPENAI_COMPAT_API_KEY`, `OPENAI_COMPAT_MODEL` | — | | Cualquier API compatible con OpenAI (OpenRouter, Ollama local…). La llave es opcional. |
| `OPENAI_COMPAT_RESPONSE_FORMAT` | `json_schema` | `json_schema`, `json_object` | `json_object` para servidores que no aceptan esquemas. |
| `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES` | `20` (`30` Workers AI), `3` | | Comunes a los proveedores HTTP. |
| `NEWS_SOURCE` | `rss` | `rss`, `cryptopanic` | De dónde salen los titulares. |
| `NEWS_RSS_FEEDS` | CoinDesk, Cointelegraph, Decrypt | URLs separadas por coma | Feeds RSS/Atom gratis. |
| `NEWS_RSS_MAX_AGE_HOURS` | `24` | | Solo titulares recientes. |
| `CRYPTOPANIC_TOKEN`, `CRYPTOPANIC_PLAN` | —, `developer` | | Solo con `NEWS_SOURCE=cryptopanic`. |
| `NEWS_REFRESH_SECONDS` | `300` | | Cada cuánto corre el NewsAgent en el worker. |
| `NEWS_VETO_HIGH_HOURS`, `NEWS_VETO_CRITICAL_HOURS` | `4`, `24` | | Duración de los vetos. |
| `SETTINGS_SOURCE` | `env` | `env`, `worker-api` | De dónde salen los interruptores en caliente (abajo). |
| `WORKER_API_URL`, `WORKER_SERVICE_TOKEN` | — | | Solo con `SETTINGS_SOURCE=worker-api`. |
| `SETTINGS_REFRESH_SECONDS` | `300` | | Caché de los interruptores remotos. |

Combinaciones típicas:

| Escenario | Variables | Costo |
|---|---|---|
| Todo apagado (default) | — | 0 |
| Noticias gratis sin LLM | `NEWS_ENABLED=true` | 0 (RSS + palabras clave) |
| Noticias con Workers AI | `NEWS_ENABLED=true LLM_PROVIDER=workers-ai CF_ACCOUNT_ID=… CF_AI_API_TOKEN=…` | 0 dentro de las 10,000 neurons diarias |
| CryptoPanic + Groq | `NEWS_ENABLED=true NEWS_SOURCE=cryptopanic CRYPTOPANIC_TOKEN=… LLM_PROVIDER=groq GROQ_API_KEY=…` | 0 en los tiers gratuitos |
| Explicaciones con LLM | `EXPLAINER_ENABLED=true` + cualquier `LLM_PROVIDER` | igual que el proveedor |

Si el proveedor elegido no tiene sus llaves, el worker lo registra y sigue **sin LLM** (palabras clave y plantilla), no se cae.

### Interruptores en caliente (`tm_agents.orchestrator.settings`)

El worker consulta un `SettingsProvider` (`get_bool("news_enabled")`, `get_bool("explainer_enabled")`) en cada ciclo:

- `EnvSettingsProvider` (default): lee `NEWS_ENABLED` y `EXPLAINER_ENABLED`.
- `WorkerApiSettingsProvider` (`SETTINGS_SOURCE=worker-api`): **hook** para el interruptor del admin. Hace `GET {WORKER_API_URL}/internal/settings` con `Authorization: Bearer {WORKER_SERVICE_TOKEN}` contra la API en Cloudflare Workers, que lee la tabla `settings` de D1. Acepta `{"news_enabled": true}`, `{"settings": {...}}` o filas `[{"key": "news_enabled", "value": "true"}]`. Guarda la respuesta `SETTINGS_REFRESH_SECONDS`; si la API falla usa los últimos valores conocidos y, si nunca respondió, los del entorno. El endpoint lo implementa la API.

`NewsService` (`tm_agents.news.service`) aplica el interruptor: apagado, no arma el NewsAgent y deja `news=None`; prendido, lo arma la primera vez, lo conecta a la capa compartida y corre `refresh` cada `NEWS_REFRESH_SECONDS`. El ExplainerAgent revisa su interruptor en cada explicación.

### LLMClient

`tm_agents.llm` expone `LLMClient.complete_json(prompt, schema)`, que devuelve una instancia Pydantic ya validada o lanza `LLMError`. `build_llm_client()` lee `LLM_PROVIDER` y devuelve `None` con `none`.

| `LLM_PROVIDER` | Implementación |
|---|---|
| `workers-ai` | `WorkersAIClient`: `POST https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{modelo}` con `response_format=json_schema` (modo JSON). |
| `groq` | `GroqClient` (API compatible con OpenAI, `response_format=json_schema`, `reasoning_effort=low`). |
| `openai-compatible` | `OpenAICompatibleClient` (`POST {base}/chat/completions`). |
| `fake` | `FakeLLM` (respuestas predefinidas, para pruebas). |

Todos reintentan con backoff exponencial (respetan `Retry-After`) ante timeouts, errores de red, 408/409/429/5xx, JSON inválido o fuera del esquema; los demás 4xx fallan de inmediato.

```python
from tm_agents.llm import build_llm_client

llm = build_llm_client()  # None con LLM_PROVIDER=none
if llm is not None:
    result = llm.complete_json("...", MiEsquema)
```

### NewsAgent

`tm_agents.news` baja titulares (RSS gratis por defecto o CryptoPanic), los clasifica (impacto `low`/`high`/`critical`, activos afectados y sentimiento en [-1, 1]) y **solo veta nuevas entradas**; nunca abre ni cierra operaciones.

- **Fuente RSS** (`RSSNewsSource`): parsea RSS 2.0 y Atom con `xml.etree`, se queda con lo publicado en las últimas `NEWS_RSS_MAX_AGE_HOURS` horas que mencione BTC, ETH o las stablecoins (por ticker o por nombre). Si un feed falla se ignora; si fallan todos, fail-open.
- **Clasificador**: con LLM (`LLMHeadlineClassifier`) o, con `LLM_PROVIDER=none`, por palabras clave (`KeywordClassifier`): `depeg`, `insolvency`, `bankruptcy`, `halts withdrawals`… → `critical`; `hack`, `exploit`, `breach`, `stolen`, `SEC`/`CFTC`/`DOJ`, `lawsuit`, `ban`, `delist`, `sanction`, `outage`… → `high`; lo demás `low`.

| Impacto | Alcance | Duración (desde la publicación) |
|---|---|---|
| `low` | ninguno | — |
| `high` | activo afectado (`BTC` bloquea `BTCUSDT`) | 4 h (`NEWS_VETO_HIGH_HOURS`) |
| `critical` | todo el mercado (`*`) | 24 h (`NEWS_VETO_CRITICAL_HOURS`) |

Los vetos se guardan en Redis (`REDIS_URL`, claves `tm:news:veto:<scope>` con TTL) y, si Redis no está o falla, en memoria del proceso.

**Fail-open:** si la fuente o el LLM fallan, el ciclo no veta, registra un warning y lo reintenta en el siguiente ciclo (`RefreshResult.failed_open`). El RiskAgent y los SL siguen protegiendo el capital.

```python
from tm_agents.news import news_agent_from_env

agent = news_agent_from_env()
agent.refresh(["BTCUSDT", "ETHUSDT"])  # noticias -> clasificación -> vetos
agent.active_veto("BTCUSDT")  # Veto vigente o None
```

### ExplainerAgent

`tm_agents.explainer.explainer_from_env(llm=…, enabled=…)` arma el ExplainerAgent del worker; el `BotSupervisor` se lo pasa a cada `BotRunner`. Con el interruptor apagado o sin LLM, la justificación sale de la plantilla determinista (#101) sin hacer red.

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

## Niveles de riesgo (`tm_agents.risk.RISK_TABLE`)

Tabla única de la capa por usuario (propuesta, sección 3.4; issue #78). El usuario solo elige el nivel y su capital (mínimo `MIN_CAPITAL` = 500 USDT):

| Nivel | Nombre | Drawdown máximo | Riesgo por operación | Exposición máxima |
| --- | --- | --- | --- | --- |
| 1 | Conservador | 10 % | 0.25 % | 20 % |
| 2 | Prudente | 15 % | 0.5 % | 35 % |
| 3 | Moderado | 20 % | 1 % | 50 % |
| 4 | Decidido | 30 % | 1.5 % | 70 % |
| 5 | Agresivo | 40 % | 2 % | 90 % |

`RiskLimits.from_profile(risk_level)` toma los tres límites de la tabla (el `max_drawdown` explícito solo sirve para forzar otro valor en pruebas o backtests) y `get_risk_level(n).max_loss(capital)` da la caída máxima en USDT. La migración `20261109000100_profiles_risk_levels.sql` (que deriva `profiles.max_drawdown`) y `apps/web/src/lib/onboarding/schema.ts` copian la tabla; `tests/test_risk_levels_sync.py` falla si alguna se desalinea.

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

## StrategyRegistry (ciclo de vida)

`tm_agents.registry.StrategyRegistry` guarda el estado de cada estrategia (`research → lab → shadow → active → probation → retired`, con revalidación `retired → lab`) y su historial, sobre las tablas `strategies` y `strategy_transitions` de Supabase (ver `supabase/README.md`).

```python
from tm_agents.registry import SYSTEM_ACTOR, InMemoryBackend, PostgRESTBackend, StrategyRegistry
from tm_agents.strategy_stats import StrategyState

registry = StrategyRegistry(PostgRESTBackend.from_env())  # o StrategyRegistry(InMemoryBackend())
registry.ensure("trend", {"lookbacks": [20, 50, 100]})  # nace en research
registry.transition("trend", StrategyState.LAB, SYSTEM_ACTOR, "entra al laboratorio")
registry.record_lab_report("trend", {"sharpe_oos": 0.8, "max_drawdown_p95": 0.22})
registry.transition("trend", StrategyState.SHADOW, SYSTEM_ACTOR, "pasa los criterios")
registry.transition("trend", StrategyState.ACTIVE, user_id, "7 días en shadow sin fallas")
registry.active(), registry.shadow(), registry.trading()  # trading = active + probation
registry.history("trend")  # [StrategyTransition(from_state, to_state, actor, ...), ...]
```

- `transition(name, to, actor, reason)` solo acepta las transiciones de `ALLOWED_TRANSITIONS` y lanza `TransitionError` en cualquier otra. Las promociones `shadow → active` y `probation → active` exigen un actor humano (el `user_id`, UUID); con `SYSTEM_ACTOR` fallan. Las degradaciones y los retiros pueden ser automáticos o manuales. La razón es obligatoria.
- Si el estado cambió entre la lectura y la escritura (otro proceso llegó antes), se lanza `StaleStateError`; la base exige que `from_state` sea el estado actual.
- La base vuelve a validar todo (checks y triggers), así que las reglas se respetan aunque otro servicio escriba directo con la llave `service_role`.
- `PostgRESTBackend.from_env()` lee `SUPABASE_URL` y `SUPABASE_SERVICE_ROLE_KEY` (la llave `service_role` en JWT o la nueva `sb_secret_...`; ambas salen de `supabase status -o env`). Las pruebas usan `InMemoryBackend` y `respx`, sin red.

## Portafolio de referencia y modo shadow (`tm_agents.reference`)

Cada estrategia en `shadow`, `active` o `probation` tiene un **ledger de referencia** a nivel app (capa compartida, propuesta, sección 3.4). Corre a tamaño completo (100 % del sleeve, 100,000 USDT nominales repartidos por igual entre BTCUSDT y ETHUSDT) con las mismas señales que reciben los bots. El LifecycleMonitor juzga a la estrategia con este portafolio y no con el P&L de ningún usuario.

```python
from tm_agents.reference import REFERENCE_CHECKPOINT, PostgRESTReferenceStore, ReferenceService
from tm_agents.registry import PostgRESTBackend, StrategyRegistry
from tm_agents.signals import RedisSignalSource

svc = ReferenceService(
    StrategyRegistry(PostgRESTBackend.from_env()),
    PostgRESTReferenceStore.from_env(),
    RedisSignalSource(redis_client, checkpoint_key=REFERENCE_CHECKPOINT),
)
svc.run_once()  # el worker `python -m tm_agents` lo llama cada HEARTBEAT_INTERVAL (REFERENCE_SERVICE=0 lo apaga)
```

- **Señales** (`tm_agents.signals`, el único módulo de `tm:signals`): el servicio lee el mismo `SignalEvent` que publica la capa compartida del orquestador (#19) y que consumen los BotRunner (formato en `docs/arquitectura/orquestador.md`). Del evento usa `strategy`, `symbol`, `bar_close`, `price` (el cierre de la vela, precio del fill) y `signal`; ignora el veto, el gate y el `catalog`, porque mide a la estrategia y no a un usuario. Lee con su propio cursor (`RedisSignalSource` con `checkpoint_key=REFERENCE_CHECKPOINT`, `tm:reference:last_signal_id`), así que no le quita entradas a los bots, y tras un reinicio retoma desde la última señal confirmada. Para publicar a mano: `publish_signal(client, SignalEvent.from_signal(signal, price))`; en pruebas, `ListSignalSource`.
- **Ejecución** (`ledger.py`): fill al cierre de la vela con 0.1 % de comisión por lado. Solo se opera cuando cambia la exposición objetivo: `long` = comprado y `flat` (o `short`, que en spot no se opera) = USDT. La exposición es la del laboratorio de cada estrategia: `trend` usa la `confidence` (fracción de canales Donchian en largo) y las demás invierten todo el sub-sleeve. Un trade va de posición 0 a posición 0 y su retorno es `pnl / costo`, igual que en el laboratorio. Una prueba compara el ledger con `simulate_sleeve` y obtiene el mismo equity y los mismos trades.
- **Métricas** (comparables con las del laboratorio): equity, drawdown actual y máximo, retorno, CAGR (desde 30 días), Sharpe diario anualizado con 365 días, trades, win rate, comisiones y Sharpe de los últimos 30 trades. Junto a ellas van el Sharpe OOS y el p95/p99 del drawdown del laboratorio (`lab.drawdown_vs_p95`). Se guardan en `strategies.live_metrics` con el estado del ledger (`ledger`), para retomarlo tras un reinicio.
- **LifecycleMonitor cableado**: `check` corre cada vez que se cierra un trade de referencia y una vez al día. Si propone una transición, se aplica con `StrategyRegistry.transition(..., actor="system")` y queda en el log (`LifecycleMonitor: 'trend' active -> probation (actor=system): ...`). Las promociones nunca son automáticas. La banda del Monte Carlo sale de `lab_report`, en el formato del walk-forward (`montecarlo.max_drawdown.p95/p99`) o en el plano (`max_drawdown_p95/p99`).
- **Persistencia** (`store.py`): `reference_trades` (trades cerrados) y `reference_equity` (un punto por día UTC), con escrituras idempotentes. Si la base falla, los datos quedan pendientes y se reintentan en el siguiente ciclo; el checkpoint del stream no avanza hasta que todo se guardó.
- **Días en shadow sin fallas** (página #58): `live_metrics.shadow` trae `started_at` (entrada a shadow, cuando empieza la corrida), `clean_since`, `days_clean`, `min_days` (7), `eligible_for_promotion`, `failures` y la última falla. Una falla es una señal que no se pudo aplicar (de un símbolo que el ledger no opera o un error al aplicarla) o un silencio de más de 3 velas de su timeframe. En la base, `GET /strategies?select=*,shadow_days_clean` lo calcula al momento.
- **Shadow nunca envía órdenes**: este servicio no tiene cliente del exchange ni escribe en `tm:decisions`. Además, `TradingSleeveGuard` (`guard.py`) se le pasa a `AgenticStrategy` del motor: solo generan órdenes los sleeves cuya estrategia está en `active` o `probation`, y cualquier otro (shadow, desconocido o con el registro inaccesible) se rechaza. Hay pruebas de las dos cosas (`test_reference_service.py` y `services/engine/tests/test_shadow_guard.py`).
