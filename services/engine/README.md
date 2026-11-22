# engine

Motor de ejecución y backtest sobre [NautilusTrader](https://nautilustrader.io) `1.231.0`
(versión exacta en `pyproject.toml`).

```bash
uv run --package tm-engine python -m tm_engine          # en bucle, con heartbeat
uv run --package tm-engine python -m tm_engine --once   # imprime su estado y termina
```

## Piezas

| Módulo | Qué hace |
| --- | --- |
| `strategy.py` | `AgenticStrategy` (subclase de `Strategy`): recibe `TradeDecision` y manda las órdenes. |
| `service.py` | `TradingEngineService`: `start(profile)`, `stop()`, `submit(decision)`, `get_positions()`. |
| `backtest.py` | `build_engine` / `run_backtest`: `BacktestEngine` con un venue simulado de Binance Spot. |
| `decisions.py` | Sobre `DecisionEnvelope` y fuentes de decisiones (Redis, programada para backtest). |
| `sleeves.py` | `SleeveLedger`: posición, costo promedio y P&L por estrategia (sleeve). |
| `recorder.py` | Registro de fills: en memoria o en la tabla `trades` (D1) vía la API (`WorkerApiTradeRecorder`). |
| `backtest_jobs.py` | `BacktestJobRunner`: backtests encolados desde el frontend (`/internal/backtests`). |
| `instruments.py` | `CurrencyPair` de BTCUSDT y ETHUSDT y conversión de velas de `tm_marketdata`. |

## Órdenes (spot)

- **Compra** → orden *bracket*: entrada `MARKET` (OTO) y, cuando se llena, un stop-loss
  `STOP_MARKET` y un take-profit `LIMIT` ligados como OUO (el que se llena cancela al otro,
  equivalente a un OCO). Los dos hijos son `reduce_only`.
- **Venta** → en spot significa quedarse en USDT (*flat*): se cancelan el SL y el TP del sleeve
  y se vende a mercado, `reduce_only`, toda la cantidad que tiene ese sleeve. Nunca se abren
  cortos: una venta sin posición se rechaza.
- Se rechazan también: decisiones con `rationale.risk.approved = false`, una segunda compra
  del mismo sleeve con posición abierta, símbolos sin instrumento y compras sin saldo libre.
- El adaptador de Binance de Nautilus no manda listas de órdenes ligadas (OCO) a Binance Spot,
  así que en vivo el SL y el TP se **emulan** localmente (`emulation_trigger="BID_ASK"`) y la
  estrategia corre con `manage_contingent_orders=True`. En el backtest el motor de matching
  simulado soporta OTO/OUO de forma nativa.

## Sleeves

Cada orden lleva los tags `sleeve:<nombre>` y `role:<entry|sl|tp|exit>`. Nautilus junta las
posiciones por instrumento (OMS *netting*), así que `SleeveLedger` lleva aparte la cantidad, el
costo promedio, las comisiones y el P&L realizado de cada sleeve a partir de los fills.
`get_positions()` devuelve los dos niveles: `{"sleeves": {...}, "venue": [...]}`.

## Decisiones por Redis (`tm:decisions`)

El orquestador publica cada decisión en el **stream** `tm:decisions` con un solo campo `data`
que contiene un `DecisionEnvelope` en JSON:

```text
XADD tm:decisions * data '{"id": "d-123", "sleeve": "trend", "decision": { ...TradeDecision... }}'
```

| Campo | Tipo | Descripción |
| --- | --- | --- |
| `id` | `str \| null` | Identificador opcional para trazabilidad. |
| `sleeve` | `str` (`[a-z0-9_-]`, ≤ 64) | Estrategia dueña del capital. Si falta, se usa el `source` de la primera señal del `rationale`, o `default`. |
| `decision` | objeto | `TradeDecision` de `tm_shared` serializado con `model_dump(mode="json")`. |

Desde Python: `tm_engine.decisions.publish_decision(redis_client, envelope)`. El motor lee
desde el último id existente al arrancar (no reprocesa decisiones viejas), descarta con un
aviso en el log las entradas malformadas y revisa el stream cada `poll_interval_secs` (1 s) y
en cada vela. Si Redis no está disponible sigue funcionando con `submit()`.

## Modos de ejecución: TESTNET, simulado y sin conexión

`ENGINE_EXECUTION` elige cómo se ejecutan las decisiones:

| Valor | Qué hace |
| --- | --- |
| `auto` (por omisión) | `testnet` si hay `BINANCE_TESTNET_API_KEY`/`SECRET`; si no, `sim`. |
| `testnet` | `TradingNode` de NautilusTrader contra Binance Spot Testnet (abajo). Sin llaves queda `offline`. |
| `sim` | `tm_engine.sim.SimExecutionEngine`: sin Nautilus ni llaves, llena cada decisión al precio actual de la REST **pública** de Binance (`api.binance.com`, respaldo `data-api.binance.vision` y luego el último precio conocido) con la comisión del laboratorio (0.1 % por lado, `ENGINE_SIM_FEE`), vigila el SL/TP y registra los fills por la API igual que el modo real, con `rationale.execution = "sim"`. Mismas reglas de aceptación que `AgenticStrategy` (shadow, guardia de sleeves, RiskAgent, sin cortos). Es lo que usa `make dev` sin llaves ([`docs/correr-local.md`](../../docs/correr-local.md)). |
| `offline` | No ejecuta nada: solo acepta decisiones y lo reporta en el latido. |

En modo `testnet`, `TradingEngineService` arma un `TradingNode` con el adaptador de Binance Spot en
`BinanceEnvironment.TESTNET` (datos y ejecución), con las llaves de:

| Variable | Uso |
| --- | --- |
| `BINANCE_TESTNET_API_KEY`, `BINANCE_TESTNET_API_SECRET` | Llaves de https://testnet.binance.vision. Si falta alguna, `ENGINE_EXECUTION=auto` usa la ejecución simulada (`sim`). |
| `REDIS_URL` | Stream de decisiones. |
| `ENGINE_SYMBOLS` (`BTCUSDT`), `ENGINE_TIMEFRAME` (`1m`), `ENGINE_PROFILE` (`default`) | Perfil de ejecución. |
| `TM_API_URL` + `TM_SERVICE_TOKEN` (+ `CF_ACCESS_CLIENT_ID`/`CF_ACCESS_CLIENT_SECRET`) | API en Cloudflare Workers: fills en `trades`, guardia de sleeves, latido y backtests (abajo). Sin ellas los fills solo quedan en memoria. |
| `ENGINE_BOT_ID` | Bot de respaldo para fills sin `bot_id` (opcional; cada decisión ya trae su bot). |
| `HEARTBEAT_FILE` (`/tmp/heartbeat`), `HEARTBEAT_INTERVAL` (`10`) | Heartbeat del contenedor. |

El heartbeat es un JSON con el estado:
`{"ts": ..., "mode": "testnet" | "sim" | "offline", "connected": false, "error": ..., ...}`
(en `sim` trae además `sim: {fills, fee, price_source, positions}`).
Los logs salen por stdout en **JSON estructurado** (en la Mac: `make runner-logs`; Docker los
rota con `max-size: 10m`). El APM del proyecto es Workers Observability del lado de la API;
del motor, la API guarda el latido (`/api/engine/status`). Los de Nautilus van
sin colores por el mismo stream. Cada línea trae `ts`, `level`, `service`, `logger`, `message`
y los campos extra que pase quien loguea (`symbols`, `timeframe`, etc.).

## Conexión con la API en Cloudflare Workers (#116)

El motor corre en la Mac (`make runner-up`, guía en
[`docs/arquitectura/motor-en-la-mac.md`](../../docs/arquitectura/motor-en-la-mac.md)) y solo hace
conexiones **salientes**: a Binance Spot Testnet y a `/internal/*` de la API
([`docs/arquitectura/api.md`](../../docs/arquitectura/api.md)) con el cliente común
`tm_shared.api_client.WorkerApiClient` (`X-Service-Token`, headers de Access opcionales,
timeouts y reintentos con backoff). Ya no usa Supabase.

| Pieza | Endpoint |
| --- | --- |
| `WorkerApiTradeRecorder` (`recorder.py`) | `POST /internal/trades` al llenarse la entrada, `PATCH /internal/trades/{id}` en cada fill posterior y al cerrar; al arrancar, `GET /internal/trades?open=true&side=buy` restaura las posiciones (#89). |
| `TradingSleeveGuard` (de `tm_agents.reference`) | `GET /internal/strategies/{name}`: solo los sleeves en `active`/`probation` mandan órdenes. |
| `HeartbeatReporter` | `POST /internal/heartbeat` (componente `engine`, con el estado del heartbeat en `details`) cada `TM_HEARTBEAT_SECONDS` (30 s); `stopping` al apagarse. Lo muestra `GET /api/engine/status`. |
| `BacktestJobRunner` (`backtest_jobs.py`) | `GET /internal/backtests?status=queued` cada `BACKTEST_POLL_SECONDS` (60 s), `PATCH` a `running` y luego a `done` (con `report`) o `failed` (con `error`). Corre en su propio hilo para no frenar el latido. `BACKTEST_JOBS=0` lo apaga. |

Un `POST /internal/trades` que falla con `5xx` o *timeout* de lectura **no se reintenta**
(podría duplicar la fila); los `PATCH` sí. El reporte de un backtest usa el simulador
vectorizado del laboratorio (`tm_engine.lab.simulator`) con los parámetros por defecto de
cada estrategia (`strategies`, por defecto `trend`), velas de `tm_marketdata` y compara contra
comprar y mantener: `metrics` (retorno, CAGR, Sharpe, volatilidad, drawdown), `benchmark`,
`trades`, `by_strategy` y la `equity` diaria (máximo ~400 puntos). D1 guarda los números
como REAL (float64): el costo promedio restaurado puede diferir en los últimos decimales.

### Correr contra el testnet real

1. Crea una llave en https://testnet.binance.vision/ (API Key + Secret).
2. Copia `.env.example` a `.env` en la raíz del repo y pon:

   ```env
   BINANCE_TESTNET_API_KEY=<tu_api_key>
   BINANCE_TESTNET_API_SECRET=<tu_api_secret>
   ENGINE_SYMBOLS=BTCUSDT
   ENGINE_TIMEFRAME=1m
   LOG_LEVEL=INFO
   ```

   `.env` está en `.gitignore`: **nunca** subas tus llaves al repo.

3. Arranca el motor (contenedor o directo):

   ```bash
   # Motor completo en la Mac con las imágenes de Docker Hub (ver motor-en-la-mac.md)
   make runner-up

   # Desarrollo: build local contra wrangler dev (cd apps/api && pnpm dev)
   make motor-local

   # Directo (necesita Rust para compilar nautilus-trader en algunas plataformas)
   uv run --package tm-engine python -m tm_engine
   ```

4. Qué esperar en `stdout` (líneas JSON, una por evento):

   ```json
   {"ts": "2026-11-17T18:00:00.000+00:00", "level": "INFO", "service": "engine",
    "logger": "tm_engine.__main__", "message": "Starting TradingNode",
    "mode": "testnet", "symbols": ["BTCUSDT"], "timeframe": "1m"}
   {"ts": "...", "level": "INFO", "service": "engine", "logger": "tm_engine.service",
    "message": "Connected to Binance Testnet", "symbols": ["BTCUSDT"], "timeframe": "1m"}
   ```

   Luego, cada minuto, una línea por vela recibida:

   ```json
   {"ts": "...", "level": "INFO", "service": "engine", "logger": "AgenticStrategy",
    "message": "Received bar BTCUSDT BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL close=63421.50"}
   ```

5. Verificar que las barras están fluyendo: filtra los logs por `"Received bar"`:

   ```bash
   docker compose logs -f engine | grep "Received bar"
   # o con jq
   docker compose logs engine | jq 'select(.message | startswith("Received bar"))'
   ```

6. Verificar el estado desde el heartbeat del healthcheck (por defecto `/tmp/heartbeat`):

   ```bash
   docker compose exec engine cat /tmp/heartbeat
   # => {"ts": ..., "mode": "testnet", "connected": true, "symbols": ["BTCUSDT"], ...}
   ```

Si `mode` sigue en `offline`, revisa el log por `faltan BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET`: casi siempre es que el `.env` no se cargó o una llave quedó en blanco.

## Backtest

```python
from tm_engine.backtest import run_backtest
from tm_engine.decisions import DecisionEnvelope, ScheduledDecisionSource

frame = MarketDataService().get_bars("BTCUSDT", "1m", start, end)
result = run_backtest({"BTCUSDT": frame}, ScheduledDecisionSource(envelopes))
result.sleeves["trend"]["realized_pnl"], result.fills, result.final_balance_usdt
```

Venue `BINANCE` simulado: cuenta `CASH` en USDT (100 000 por defecto), OMS netting, comisión
de 0.1 % por lado y ejecución sobre OHLC de las velas. Las velas se fechan a su **cierre**
(`open_time` + duración) para no usar información futura. `ScheduledDecisionSource` suelta
cada decisión cuando el reloj del motor alcanza su `decided_at`.

Las pruebas (`tests/test_backtest_scenarios.py`) cubren con velas sintéticas: estructura del
bracket, take-profit, stop-loss, venta con `reduce_only`, rechazo por riesgo, decisiones
inválidas, saldo insuficiente y P&L atribuido por sleeve.

## Laboratorio: walk-forward, holdout y Monte Carlo (`tm_engine.lab`)

```bash
# Trend, trend_vol (#83) o momentum intradía: 20 tramos de 12 meses in-sample + 3 out-of-sample
# (2021-T1 a 2025-T4); el holdout 2026 solo con --holdout
uv run --package tm-engine python -m tm_engine.lab run --strategy trend \
    --symbols BTCUSDT ETHUSDT --data-dir data/bars --out-dir reports/lab

# Kronos: elige `min_return` con jul-dic 2024 usando data/forecasts (precalculados)
uv run --package tm-engine python -m tm_engine.lab kronos --data-dir data/bars \
    --forecasts-dir data/forecasts --out-dir reports/lab
```

| Módulo | Qué hace |
| --- | --- |
| `lab/config.py` | `WalkForwardConfig` (tamaños de ventana configurables) y la guarda del holdout: sin `include_holdout=True` (`--holdout`) no se cargan velas de 2026 y pedir ventanas que lo crucen lanza `HoldoutError`. |
| `lab/strategies.py` | Adaptadores de `TrendAgent` (con y sin volatility targeting, `trend_vol`) e `IntradayMomentumAgent`: series objetivo (1 = largo, 0 = USDT) con las mismas funciones que `analyze`, y la cuadrícula in-sample de cada una. `trend_vol` reparte cada periodo en 10 lotes para tener exposición fraccional. |
| `lab/simulator.py` | Simulador vectorizado de un sleeve spot con comisión por lado; equity a mercado por vela y diario. |
| `lab/execution.py` | Convierte las órdenes en `TradeDecision` y las corre en `run_backtest` (Nautilus). |
| `lab/walkforward.py` | `walk_forward(config)`: cuadrícula por Sharpe in-sample, out-of-sample en Nautilus, comparación con comprar y mantener, `bootstrap` del drawdown a 1 año (365 días; el horizonte completo es referencia) y `evaluate`. |
| `lab/report.py` | JSON (con la serie diaria de equity), Markdown y PNG. |
| `lab/kronos.py` | Umbral de Kronos en jul-dic 2024 con los pronósticos precalculados, out-of-sample 2025 y holdout 2026 detrás de la bandera. |

La cuadrícula in-sample (20 ventanas × hasta 36 combinaciones) corre en el simulador vectorizado; el
tramo out-of-sample completo se ejecuta en Nautilus y el reporte registra la diferencia entre los
dos (en los backtests reales fue < 1e-11 relativo). La prueba
`test_nautilus_matches_vectorized_simulator` valida la equivalencia orden por orden en un caso
pequeño. Resultados: `docs/estrategias/resultados-trend.md` y
`docs/estrategias/resultados-intraday-momentum.md`.
