# Arquitectura end-to-end

Vista de alto nivel del sistema completo. Para el diseño conceptual, lee la [propuesta](../propuesta/Propuesta-trading_maxxing.md) (secciones 3.2, 3.3 y 3.4). Para el detalle del flujo por vela, lee [arquitectura/orquestador.md](../arquitectura/orquestador.md).

## Componentes

```
┌──────────────────────────────── trading_maxxing ─────────────────────────────────┐
│                                                                                   │
│  apps/web (Next.js 15)                                                            │
│      └─ Supabase Auth + Realtime                                                  │
│      └─ fetch /api/...  ──────────────────────────┐                               │
│                                                   ▼                               │
│  services/api (FastAPI) ──────────┬─ PostgREST (Supabase) ─ RLS por usuario        │
│      │                            └─ tm:commands (Redis)                          │
│      ▼                                                                            │
│  services/agents (worker)                                                         │
│   ├─ capa compartida: NewsAgent, estrategias vivas, SimulationGate ─▶ tm:signals  │
│   ├─ capa por usuario: un BotRunner por bot ──▶ tm:decisions / tm:shadow          │
│   └─ ReferenceService + LifecycleMonitor ──▶ strategies.state                     │
│                                                                                   │
│  services/forecast (FastAPI)   Kronos-small sobre PyTorch (CPU / MPS)             │
│      └─ POST /predict, /predict/batch ─▶ KronosAgent y SimulationGate             │
│                                                                                   │
│  services/engine (NautilusTrader)                                                 │
│   ├─ AgenticStrategy: tm:decisions ──▶ órdenes bracket (SL/TP) en Binance Testnet │
│   └─ SupabaseTradeRecorder: fills ──▶ tabla trades                                │
│                                                                                   │
│  packages/shared        Contratos Pydantic (Signal, Decision, enums)              │
│                                                                                   │
└───────────────────────────────────────────────────────────────────────────────────┘

                    Redis streams (tm:signals, tm:decisions, tm:shadow,
                    tm:commands, tm:backtests, vetos del NewsAgent)

                    Supabase (Postgres + Auth + Realtime + RLS)
```

Las responsabilidades por servicio están en sus READMEs:

- [`services/api/README.md`](../../services/api/README.md)
- [`services/agents/README.md`](../../services/agents/README.md) y [`docs/arquitectura/orquestador.md`](../arquitectura/orquestador.md)
- [`services/engine/README.md`](../../services/engine/README.md)
- [`services/forecast/README.md`](../../services/forecast/README.md)
- [`apps/web/README.md`](../../apps/web/README.md)
- [`supabase/README.md`](../../supabase/README.md)
- [`infra/README.md`](../../infra/README.md)

## Diagramas

- [Clases](../propuesta/img/classes.png) · fuente: [`docs/propuesta/classes.mmd`](../propuesta/classes.mmd)
- [Infraestructura (AWS)](../propuesta/img/infra.png) · fuente: [`docs/propuesta/infra.py`](../propuesta/infra.py)
- [Deployment](../propuesta/img/deploy.png) · fuente: [`docs/propuesta/deploy.py`](../propuesta/deploy.py)
- [Estrategia de ramas](../propuesta/img/branches.png) · fuente: [`docs/propuesta/branches.mmd`](../propuesta/branches.mmd)

## Flujo de datos (ingest → venue)

1. **Ingest.** `tm_marketdata` baja velas históricas de Binance (BTC/USDT y ETH/USDT, varios timeframes) y las guarda en Parquet bajo `data/` (ver `scripts/` dentro de `services/forecast` y el módulo `tm_marketdata`). En vivo, `MarketDataBarsProvider` del motor y la capa compartida leen velas cerradas desde Binance.
2. **Capa compartida del orquestador** (una vez para todos). Por cada cierre de vela y cada estrategia viva:
   - Arma el `AgentContext` con velas cerradas (sin look-ahead).
   - Pide la `Signal` a la estrategia (`TrendAgent`, `KronosAgent`, `IntradayMomentumAgent`).
   - Consulta el veto del `NewsAgent` (Redis). Fail-open si Redis responde con error.
   - Si la señal es `long`, corre la `SimulationGate` (bootstrap histórico + trayectorias Kronos).
   - Publica un `SignalEvent` en `tm:signals`.
3. **Capa por usuario.** `BotSupervisor` sincroniza cada minuto la tabla `bots` (los que están `running`). Cada bot tiene su `BotRunner`, que por evento:
   - Calcula peso del sleeve con `PortfolioAllocator`, exposición por nivel de riesgo y posición objetivo.
   - Pasa por `RiskAgent` (exposición máxima, pérdida del día, kill switch).
   - Publica un `DecisionEnvelope` en `tm:decisions` con `bot_id`, `sleeve` y `target_quantity`.
4. **Engine.** `AgenticStrategy` (NautilusTrader) convierte el `DecisionEnvelope` en una orden bracket (entry `MARKET` + SL `STOP_MARKET` + TP `LIMIT`, los hijos como `reduce_only`). En backtest usa `BacktestEngine`; en vivo, el `TradingNode` contra Binance Spot Testnet.
5. **Persistencia y realtime.** El `SupabaseTradeRecorder` escribe cada fill en `trades` con la llave `service_role`. Supabase Realtime publica `trades`, `equity_snapshots`, `bots` y `strategies`: el dashboard de la web (Next.js 15, estático) se actualiza vía websocket contra Supabase sin pasar por la API.

## Ciclo de vida de estrategias

Idéntico al de la propuesta (sección 3.3):

```
investigación → laboratorio → shadow → activa → en observación → retirada
                    ▲                                                │
                    └───────────────── revalidación ─────────────────┘
```

| Estado | Opera | Transición permitida |
|---|---|---|
| `research` | No | → `lab`, `retired` |
| `lab` | No (walk-forward + Monte Carlo) | → `shadow`, `research`, `retired` |
| `shadow` | Virtual (`tm:shadow`, libro aparte) ≥ 7 días | → `active` (humano), `lab`, `retired` |
| `active` | Sí, con el peso del `PortfolioAllocator` | → `probation`, `retired` |
| `probation` | Sí, con la mitad del peso | → `active` (humano), `retired` |
| `retired` | No | → `lab` (revalidación) |

- El código entra por PR. Las promociones a `active` las aprueba un admin (`POST /strategies/{name}/promote`). Las degradaciones son automáticas desde el `LifecycleMonitor` (`actor='system'`): se degrada si el drawdown en vivo pasa el p95 del Monte Carlo del laboratorio y se retira si pasa el p99.
- El estado es un dato en la tabla `strategies` de Supabase, y cada cambio deja un registro en `strategy_transitions` (quién, cuándo, por qué). El trigger de la base bloquea cambios con `from_state` desactualizado para evitar carreras.
- El motor consulta el `TradingSleeveGuard` antes de cada **entrada**: solo `active` y `probation` abren posición; `shadow`, `research`, `lab`, `retired` o un error del registro se rechazan (fail-closed). Los **cierres** pasan siempre, incluso si la estrategia quedó retirada con posición abierta.

Detalle operativo en [arquitectura/orquestador.md](../arquitectura/orquestador.md).

## Persistencia (Supabase)

Esquema completo en [`supabase/README.md`](../../supabase/README.md). Resumen:

| Tabla | Dueño | Lo que guarda |
|---|---|---|
| `profiles` | usuario (`user_id = auth.uid()`) | nivel de riesgo (1–5), capital, horizonte, instrumentos. `max_drawdown` lo deriva un trigger. |
| `goals` | usuario vía `profile_id` | retorno objetivo, fecha límite. |
| `bots` | usuario vía `profile_id` | nombre, `bot_status`, `strategy_config`. |
| `trades` | lectura por usuario, escritura con `service_role` | fills reales del motor: `rationale.position`, SL/TP, P&L, abierto si `closed_at IS NULL`. |
| `equity_snapshots` | lectura por usuario, escritura con `service_role` | fotos de equity por bot. |
| `backtests` | usuario vía `profile_id` | config y reporte de walk-forward. |
| `strategies` | catálogo global (lectura para `authenticated`) | estado del ciclo de vida, `lab_report`, `live_metrics`. |
| `strategy_transitions` | catálogo global, solo inserción | historial de transiciones; `authenticated` solo lee. |
| `app_roles` | lectura del propio por el usuario | `admin` o `member`. Sin fila cuenta como `member`. |

Restricciones principales:
- `profiles.risk_level` entre 1 y 5, `capital >= 500` USDT de testnet.
- `trades`: cantidades y precios positivos, `fees >= 0`, `exit_price` y `closed_at` van juntos, `closed_at >= opened_at`, stop y objetivo del lado correcto de la entrada.
- `backtests`: `done` exige `report`, `failed` exige `error`.
- RLS en todas las tablas. Tras un fallo de autorización PostgREST devuelve 404 sobre la fila ajena, no 403.

**Fuente de verdad de los libros.** La tabla `trades` manda. El libro virtual del `BotRunner` y el libro por `bot/sleeve` del motor son derivados y se reconstruyen al arrancar:

- `BotSupervisor` lee las filas del bot antes de arrancar su runner (ver `PostgRESTTradeStateRepository`); si la lectura falla, el bot no arranca y reintenta.
- `TradingEngineService._restore_positions` siembra el libro por `bot/sleeve` con `SupabaseTradeRecorder.open_positions()` para que un cierre tardío no quede huérfano.

**Limitación conocida (del issue #89 y pendientes en `docs/estado-del-proyecto.md`).** Los SL/TP emulados localmente no sobreviven a un reinicio del motor: las posiciones restauradas quedan sin tramos y se cierran con la siguiente venta del bot (a mercado).

## Mensajería (Redis)

Streams, con formato definido en `tm_agents.signals` y en `tm_agents.orchestrator.streams`:

| Stream | Publica | Consume | Formato |
|---|---|---|---|
| `tm:signals` | capa compartida | `BotRunner`, `ReferenceService` | `SignalEvent` (JSON en campo `data`) |
| `tm:decisions` | `BotRunner` | motor | `DecisionEnvelope` con `bot_id`, `sleeve`, `target_quantity` |
| `tm:shadow` | `BotRunner` en shadow | logs / debug | mismo sobre con `shadow=true`. El motor lo rechaza. |
| `tm:commands` | API | `BotSupervisor` | `type` (`start`, `stop`, `kill`), `bot_id`, `user_id`, `reason`, `payload` |
| `tm:backtests` | API | worker de backtests | `backtest_id`, `profile_id`, `config` |
| vetos del `NewsAgent` | `NewsAgent` | capa compartida | hash por símbolo (`scope`, `impact`, `expires_at`) |

Los cursores (`tm:reference:last_signal_id`, checkpoints del `RedisSignalSource`) permiten retomar desde el último id tras un reinicio del worker.

## Secretos

Resumen; el detalle operativo está en [setup-local.md](setup-local.md).

- **Nunca en el repo.** `.env` está en `.gitignore`. El archivo de referencia es `.env.example`.
- **Local.** Las llaves de Supabase se obtienen con `supabase status`; son llaves de demo iguales en cualquier máquina. Las de Binance Testnet, Groq y CryptoPanic las pone cada quien en su `.env`.
- **GitHub Actions.** Los secrets `DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN` habilitan el push a Docker Hub (sin ellos el workflow solo construye y lo avisa). El deploy a Cloudflare Pages se salta en verde si faltan `CLOUDFLARE_API_TOKEN` o `CLOUDFLARE_ACCOUNT_ID`.
- **AWS.** El workflow de Terraform corre `fmt`, `validate` y `tflint` sin credenciales; el `plan` se salta si faltan `AWS_PLAN_ROLE_ARN` (OIDC) o las credenciales temporales de AWS Academy.
- **Nube (pendiente, cuando haya cuenta).** El módulo `infra/modules/secrets` crea los secretos en AWS Secrets Manager (Binance Testnet, Supabase, Groq, CryptoPanic). ECS Fargate los inyecta como variables de entorno al arrancar las tareas.
- **Servicio API.** Si llega una petición con un JWT inválido o con una llave ajena, PostgREST + RLS la contesta con 404 (no filtra filas). La API solo usa `service_role` para `POST /admin/users`.

## Variables de entorno principales

Las tres piezas del worker `agents` se controlan con flags (todas corren por omisión):

| Variable | Default | Apaga con `0` | Necesita |
|---|---|---|---|
| `ORCHESTRATOR_SHARED` | `1` | capa compartida (publica `tm:signals`) | `REDIS_URL` |
| `ORCHESTRATOR_BOTS` | `1` | capa por usuario (`tm:signals` → `tm:decisions`) | `REDIS_URL` |
| `REFERENCE_SERVICE` | `1` | ledger de referencia y `LifecycleMonitor` | `REDIS_URL`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` |

Lista completa en [`.env.example`](../../.env.example).

## Backtest vs. en vivo

Las mismas clases corren en los dos modos; solo cambian la fuente de velas y los streams:

| | En vivo | Backtest |
|---|---|---|
| Velas | `MarketDataBarsProvider` | `FrameBarsProvider` (DataFrames) |
| Estrategias | `StrategyRegistry` con `PostgRESTBackend` | `StrategyRegistry` con `InMemoryBackend` |
| `tm:signals` | `RedisSignalSink` / `RedisSignalSource` | `run_offline` o `ListSignalSource` |
| `tm:decisions` | `RedisStreamSink` | `MemorySink` → `ScheduledDecisionSource` |
| Venue | TradingNode contra Binance Testnet | `BacktestEngine` con venue simulado |

`services/engine/tests/test_orchestrator_backtest.py` prueba de punta a punta que la posición de cada bot en el motor coincide con su libro virtual.
