# Arquitectura end-to-end

Vista de alto nivel del sistema completo. Para el diseño conceptual, lee la [propuesta](../propuesta/Propuesta-trading_maxxing.md) (secciones 3.2, 3.3 y 3.4). Para el detalle del flujo por vela, lee [arquitectura/orquestador.md](../arquitectura/orquestador.md). Para el contrato con el curso, ver la [rúbrica oficial](../rubrica.md) (PR #104).

> **Actualización del 17 de noviembre de 2026.** La rúbrica solo acepta **Azure Portal o Cloudflare** como nube, y evalúa métricas del **App Service Plan**. Por eso el backend se despliega en **Azure App Service** (API y agents) con **Application Insights** para el APM, y el frontend en **Cloudflare Pages**. El Terraform vive en un repo aparte, **`trading_maxxing-infra`**, con el provider **`azurerm`**. La propuesta original en AWS/ECS Fargate queda documentada como antecedente histórico en [`decisiones.md`](decisiones.md).

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
- Infraestructura: repo externo [`trading_maxxing-infra`](https://github.com/CarloSantoro21/trading_maxxing-infra) (el Terraform de AWS en [`infra/`](../../infra/) del monorepo está deprecado; ver [`decisiones.md`](decisiones.md))

## Diagramas

- [Clases](../propuesta/img/classes.png) · fuente: [`docs/propuesta/classes.mmd`](../propuesta/classes.mmd)
- [Infraestructura](../propuesta/img/infra.png) · fuente: [`docs/propuesta/infra.py`](../propuesta/infra.py) *(versión histórica en AWS; la de Azure + Cloudflare se rehace en el issue [#7](https://github.com/CarloSantoro21/trading_maxxing/issues/7))*
- [Deployment](../propuesta/img/deploy.png) · fuente: [`docs/propuesta/deploy.py`](../propuesta/deploy.py) *(ídem: se rehace para Azure + Cloudflare en #7)*
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

## Despliegue (Azure App Service + Cloudflare Pages)

Arquitectura de nube desde el 17 de noviembre de 2026. El despliegue ya **no** depende de un go/no-go: la rúbrica lo exige como entregable (ver [rúbrica, sección 1](../rubrica.md) y [issue #44](https://github.com/CarloSantoro21/trading_maxxing/issues/44)).

```
GitHub (monorepo)                                 GitHub (trading_maxxing-infra)
  feature/* ─PR─▶ develop ─PR─▶ main                Terraform `azurerm`
        │             │             │                 envs/dev, envs/prod
        ▼             ▼             ▼                 Resource Group, App Service Plan (B1),
     CI (lint,   deploy dev     aprobación            Web Apps (api, agents),
     pruebas,    automático     environment           Application Insights,
     cobertura,  + UAT          `prod`                Log Analytics, Redis
     UAT)        (Playwright)   (required reviewer)
        │
        ├──▶ Docker Hub (imágenes multi-arch por SHA y por tag)
        │       └──▶ Azure App Service (API dev / prod, agents dev / prod)
        └──▶ Cloudflare Pages (frontend dev / prod)
```

### Backend — Azure App Service

- **Dos ambientes**, `dev` y `prod`, creados por Terraform en `trading_maxxing-infra` (provider `azurerm`).
- **Plan B1** (Basic, 1 vCPU, 1.75 GB RAM) por ambiente: un App Service Plan hospeda las Web Apps `api` y `agents` como contenedores Linux. El `engine` y `forecast` quedan como workers en el mismo plan cuando se promuevan (hoy se demuestran en local; ver [`docs/estado-del-proyecto.md`](../estado-del-proyecto.md)).
- **Origen de las imágenes**: Docker Hub (`trading-maxxing-api`, `trading-maxxing-agents`, etc.). El App Service hace *pull* por SHA o por tag (`develop` en dev, `vX.Y.Z` en prod).
- **Variables de entorno** se cargan como *App Settings* (vía `azurerm_linear_web_app` en el repo de infra). Los secretos se guardan en **Azure Key Vault** y la Web App los referencia con `@Microsoft.KeyVault(...)`.
- **Autoscale**: el plan B1 permite una instancia; escalado horizontal/vertical queda documentado como mejora futura.

### Frontend — Cloudflare Pages

- Mismo export estático que ya se publica desde CI (ver [`decisiones.md`](decisiones.md) §8).
- **Dos proyectos / ambientes**: `trading-maxxing-dev` y `trading-maxxing-prod`. El deploy a `dev` es automático al mergear a `develop`; el de `prod` requiere aprobación del *environment* `prod` en GitHub Actions.
- Variables `NEXT_PUBLIC_*` cambian por ambiente y se incrustan al build.

### Observabilidad

- **Application Insights** (SDK de OpenTelemetry para Python en la API y los agents): recoge **requests**, **trazas**, **excepciones** y **mensajes de log**. La API expone un **endpoint de demo** que lanza un error controlado para mostrar el flujo APM en la rúbrica (ver issue [#29](https://github.com/CarloSantoro21/trading_maxxing/issues/29)).
- **Azure Monitor**: muestra en el portal **CPU y memoria del App Service Plan** y las **requests por minuto** del backend. Dashboard por ambiente (uno para dev, otro para prod).
- **Log Analytics Workspace**: destino único para logs de App Service y Application Insights; consultas con KQL.
- Para el frontend, Cloudflare Pages ofrece Web Analytics; se referencia en la demo si hay tiempo.

### Infraestructura como código — `trading_maxxing-infra`

- Repo separado: [`trading_maxxing-infra`](https://github.com/CarloSantoro21/trading_maxxing-infra) (issues [#26](https://github.com/CarloSantoro21/trading_maxxing/issues/26) y [#39](https://github.com/CarloSantoro21/trading_maxxing/issues/39)).
- Provider `azurerm`, backend remoto en Azure Storage, dos stacks: `envs/dev` y `envs/prod`.
- Módulos: `resource-group`, `app-service-plan`, `web-app` (api, agents), `app-insights`, `log-analytics`, `key-vault`, `redis` (Azure Cache for Redis Basic C0).
- El Terraform de AWS que todavía vive en `infra/` del monorepo (PR #79) está **obsoleto**: se migra a Azure y se retira cuando el repo de infra tome el relevo.

## Secretos

Resumen; el detalle operativo está en [setup-local.md](setup-local.md).

- **Nunca en el repo.** `.env` está en `.gitignore`. El archivo de referencia es `.env.example`.
- **Local.** Las llaves de Supabase se obtienen con `supabase status`; son llaves de demo iguales en cualquier máquina. Las de Binance Testnet, Groq y CryptoPanic las pone cada quien en su `.env`.
- **GitHub Actions.** Los secrets `DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN` habilitan el push a Docker Hub (sin ellos el workflow solo construye y lo avisa). El deploy a Cloudflare Pages usa `CLOUDFLARE_API_TOKEN` y `CLOUDFLARE_ACCOUNT_ID` (uno por ambiente). El deploy a Azure App Service usa `AZURE_CREDENTIALS` (service principal con permisos `Website Contributor` sobre los dos resource groups) o, preferiblemente, **OIDC federado** con `AZURE_CLIENT_ID`, `AZURE_TENANT_ID` y `AZURE_SUBSCRIPTION_ID`.
- **Azure.** El App Service Plan lee los secretos por referencia a **Key Vault** (`@Microsoft.KeyVault(SecretUri=...)`). Los crea el Terraform de `trading_maxxing-infra` a partir de variables marcadas `sensitive = true` y el valor se inyecta desde GitHub Actions o se sube con `az keyvault secret set`.
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
