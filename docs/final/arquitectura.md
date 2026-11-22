# Arquitectura end-to-end

Vista de alto nivel del sistema completo. Para el diseño conceptual, lee la [propuesta](../propuesta/Propuesta-trading_maxxing.md) (secciones 3.2, 3.3 y 3.4). Para el detalle del flujo por vela, lee [arquitectura/orquestador.md](../arquitectura/orquestador.md). Para el contrato con el curso, ver la [rúbrica oficial](../rubrica.md) (PR #104).

> **Arquitectura final (21 de noviembre de 2026).** Todo lo que ve el usuario vive en **Cloudflare, plan gratuito**: frontend en **Pages**, API en **Workers con Hono + TypeScript** (`apps/api`), base **D1**, login con **Access** y APM con **Workers Observability**. Ya no hay Supabase. El **motor** (agents, engine con NautilusTrader, forecast con Kronos y Redis) corre en la **Mac de Carlo** con `docker compose` e imágenes de Docker Hub, y solo hace conexiones salientes a Binance Spot Testnet y a `/internal/*` de la API. El detalle está en [`docs/arquitectura/cloudflare.md`](../arquitectura/cloudflare.md); la historia (AWS → Azure → Cloudflare), en [`decisiones.md`](decisiones.md) §4, §11 y §12.

## Componentes

```
┌────────────────────────── Cloudflare (plan gratuito) ──────────────────────────────┐
│                                                                                    │
│  Access (allowlist + OTP)  ──▶  Pages tm-web-dev / tm-web (Next.js 15, estático)   │
│                                     │ /api/*  ── service binding "API" (env.API)   │
│                                     ▼                                              │
│                          Worker tm-api-dev / tm-api (apps/api, Hono + TS)          │
│                             ├─ D1 tm-dev / tm-prod (users, profiles, bots, trades, │
│                             │   equity, strategies, commands, heartbeats, settings)│
│                             └─ Workers Observability (logs, trazas, errores)       │
│                                     ▲ /internal/*  (X-Service-Token + Access)      │
└─────────────────────────────────────┼──────────────────────────────────────────────┘
                                      │ solo conexiones SALIENTES
┌──────────────── Mac de Carlo (docker compose + Watchtower) ────────────────────────┐
│  services/agents (worker)                                                          │
│   ├─ capa compartida: estrategias vivas, SimulationGate (NewsAgent opcional)       │
│   ├─ capa por usuario: un BotRunner por bot ──▶ tm:decisions / tm:shadow           │
│   └─ ReferenceService + LifecycleMonitor ──▶ estado de las estrategias (API)       │
│  services/forecast (FastAPI)   Kronos-small sobre PyTorch (CPU / MPS)              │
│  services/engine (NautilusTrader)                                                  │
│   ├─ AgenticStrategy: tm:decisions ──▶ órdenes bracket (SL/TP) ──▶ Binance Testnet │
│   └─ grabador de trades: fills ──▶ POST /internal/trades                           │
│  redis   streams tm:signals, tm:decisions, tm:shadow                               │
│  packages/shared   contratos Pydantic (Signal, Decision, enums)                    │
└────────────────────────────────────────────────────────────────────────────────────┘
```

Para correr todo esto en local de punta a punta: `make dev` ([`docs/correr-local.md`](../correr-local.md)). El estado vivo está en [`docs/estado-del-proyecto.md`](../estado-del-proyecto.md).

Las responsabilidades por servicio están en sus READMEs:

- [`apps/api/README.md`](../../apps/api/README.md) y el contrato [`docs/arquitectura/api.md`](../arquitectura/api.md)
- [`services/agents/README.md`](../../services/agents/README.md) y [`docs/arquitectura/orquestador.md`](../arquitectura/orquestador.md)
- [`services/engine/README.md`](../../services/engine/README.md)
- [`services/forecast/README.md`](../../services/forecast/README.md)
- [`apps/web/README.md`](../../apps/web/README.md)
- Infraestructura: repo externo [`trading_maxxing-infra`](https://github.com/CarloSantoro21/trading_maxxing-infra) (Terraform `cloudflare/cloudflare`: D1, Pages y Access por ambiente; el Terraform de AWS en `infra/` del monorepo está obsoleto)

## Diagramas

- [Clases](../propuesta/img/classes.png) · fuente: [`docs/propuesta/classes.mmd`](../propuesta/classes.mmd)
- [Infraestructura](../propuesta/img/infra.png) · fuente: [`docs/propuesta/infra.py`](../propuesta/infra.py) (Cloudflare + motor en la Mac)
- [Deployment](../propuesta/img/deploy.png) · fuente: [`docs/propuesta/deploy.py`](../propuesta/deploy.py) (GitHub Actions → Workers, Pages y Docker Hub)
- [Estrategia de ramas](../propuesta/img/branches.png) · fuente: [`docs/propuesta/branches.mmd`](../propuesta/branches.mmd)

## Flujo de datos (ingest → venue)

1. **Ingest.** `tm_marketdata` baja velas históricas de Binance (BTC/USDT y ETH/USDT, varios timeframes) y las guarda en Parquet bajo `data/` (ver `scripts/` dentro de `services/forecast` y el módulo `tm_marketdata`). En vivo, `MarketDataBarsProvider` del motor y la capa compartida leen velas cerradas desde Binance.
2. **Capa compartida del orquestador** (una vez para todos). Por cada cierre de vela y cada estrategia viva:
   - Arma el `AgentContext` con velas cerradas (sin look-ahead).
   - Pide la `Signal` a la estrategia (`TrendAgent`, `KronosAgent`, `IntradayMomentumAgent`).
   - Consulta el veto del `NewsAgent` (Redis) solo si las noticias están prendidas (`NEWS_ENABLED`, apagado por defecto). Fail-open si Redis responde con error.
   - Si la señal es `long`, corre la `SimulationGate` (bootstrap histórico + trayectorias Kronos).
   - Publica un `SignalEvent` en `tm:signals`.
3. **Capa por usuario.** `BotSupervisor` sincroniza cada minuto los bots en `running` (`GET /internal/bots?status=running`) y lee los comandos `start` / `stop` / `kill` con `GET /internal/commands`. Cada bot tiene su `BotRunner`, que por evento:
   - Calcula peso del sleeve con `PortfolioAllocator`, exposición por nivel de riesgo y posición objetivo.
   - Pasa por `RiskAgent` (exposición máxima, pérdida del día, kill switch).
   - Publica un `DecisionEnvelope` en `tm:decisions` con `bot_id`, `sleeve` y `target_quantity`.
4. **Engine.** `AgenticStrategy` (NautilusTrader) convierte el `DecisionEnvelope` en una orden bracket (entry `MARKET` + SL `STOP_MARKET` + TP `LIMIT`, los hijos como `reduce_only`). En backtest usa `BacktestEngine`; en vivo, el `TradingNode` contra Binance Spot Testnet.
5. **Persistencia.** El motor escribe cada fill con `POST /internal/trades` (y `PATCH` al cerrar), las fotos de equity con `POST /internal/equity-snapshots` y un latido cada 30 s con `POST /internal/heartbeat`. El dashboard (Next.js 15, estático) hace *polling* a `/api/dashboard` en su mismo dominio; ya no hay Realtime.

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

- El código entra por PR. Las promociones a `active` las aprueba un admin (`POST /api/strategies/{name}/promote`). Las degradaciones son automáticas desde el `LifecycleMonitor` (`actor='system'`): se degrada si el drawdown en vivo pasa el p95 del Monte Carlo del laboratorio y se retira si pasa el p99.
- El estado es un dato en la tabla `strategies` de D1, y cada cambio deja un registro en `strategy_transitions` (quién, cuándo, por qué). Un trigger de SQLite bloquea cambios con `from_state` desactualizado (`409 stale_state`) para evitar carreras.
- El motor consulta el `TradingSleeveGuard` antes de cada **entrada**: solo `active` y `probation` abren posición; `shadow`, `research`, `lab`, `retired` o un error del registro se rechazan (fail-closed). Los **cierres** pasan siempre, incluso si la estrategia quedó retirada con posición abierta.

Detalle operativo en [arquitectura/orquestador.md](../arquitectura/orquestador.md).

## Persistencia (D1)

Migraciones en [`apps/api/migrations/`](../../apps/api/migrations/) (port de las de Supabase a SQLite); detalle en [`api.md` §4](../arquitectura/api.md). Resumen:

| Tabla | Dueño | Lo que guarda |
|---|---|---|
| `users` | admin | correo (el de Access) y rol `admin` / `member`. Sin fila, la API responde 403. |
| `profiles` | usuario (`user_id`) | nivel de riesgo (1–5), capital, horizonte, instrumentos y `max_drawdown` derivado. |
| `goals` | usuario vía `profile_id` | retorno objetivo, fecha límite. |
| `bots` | usuario vía `profile_id` | nombre, estado, `strategy_config`. |
| `trades` | lectura por usuario, escritura del motor (`/internal`) | fills reales: posición, SL/TP, P&L; abierto si `closed_at IS NULL`. |
| `equity_snapshots` | lectura por usuario, escritura del motor | fotos de equity por bot. |
| `backtests` | usuario vía `profile_id` | config y reporte de walk-forward. |
| `strategies`, `strategy_transitions` | catálogo global | estado del ciclo de vida, reportes, historial de transiciones. |
| `settings`, `engine_heartbeats`, `commands`, `decisions`, `orchestrator_state` | API y motor | interruptores del LLM y las noticias, latidos, comandos para el motor, auditoría de decisiones y cursores. |

Restricciones principales (como `CHECK` de SQLite):
- `profiles.risk_level` entre 1 y 5, `capital >= 500` USDT de testnet.
- `trades`: cantidades y precios positivos, `fees >= 0`, `exit_price` y `closed_at` van juntos, stop y objetivo del lado correcto de la entrada.
- `backtests`: `done` exige `report`, `failed` exige `error`.
- **No hay RLS en D1**: cada consulta de `/api/*` filtra por el usuario autenticado y lo ajeno responde 404, igual que antes con RLS.

**Fuente de verdad de los libros.** La tabla `trades` manda. El libro virtual del `BotRunner` y el libro por `bot/sleeve` del motor son derivados y se reconstruyen al arrancar:

- `BotSupervisor` lee las filas del bot (`GET /internal/trades?bot_id=`) antes de arrancar su runner; si la lectura falla, el bot no arranca y reintenta.
- `TradingEngineService._restore_positions` siembra el libro por `bot/sleeve` con las posiciones abiertas (`GET /internal/trades?open=true`) para que un cierre tardío no quede huérfano.

**Limitación conocida (del issue #89 y pendientes en `docs/estado-del-proyecto.md`).** Los SL/TP emulados localmente no sobreviven a un reinicio del motor: las posiciones restauradas quedan sin tramos y se cierran con la siguiente venta del bot (a mercado).

## Mensajería (Redis, dentro de la Mac)

Streams, con formato definido en `tm_agents.signals` y en `tm_agents.orchestrator.streams`:

| Stream | Publica | Consume | Formato |
|---|---|---|---|
| `tm:signals` | capa compartida | `BotRunner`, `ReferenceService` | `SignalEvent` (JSON en campo `data`) |
| `tm:decisions` | `BotRunner` | motor | `DecisionEnvelope` con `bot_id`, `sleeve`, `target_quantity` |
| `tm:shadow` | `BotRunner` en shadow | logs / debug | mismo sobre con `shadow=true`. El motor lo rechaza. |
| vetos del `NewsAgent` | `NewsAgent` (si está prendido) | capa compartida | hash por símbolo (`scope`, `impact`, `expires_at`) |

Los comandos (`start`, `stop`, `kill`) y los backtests en cola ya no viajan por Redis: la API los guarda en D1 y el motor los lee con *polling* (`GET /internal/commands?after=<seq>` + `ack`, `GET /internal/backtests?status=queued`), porque la API vive en Cloudflare y no puede abrir conexiones hacia la Mac.

Los cursores (`tm:reference:last_signal_id`, checkpoints del `RedisSignalSource`) permiten retomar desde el último id tras un reinicio del worker.

## Despliegue (Cloudflare + motor en la Mac)

El despliegue es un entregable de la rúbrica (ver [rúbrica, sección 5](../rubrica.md)). Diagrama: [deployment](../propuesta/img/deploy.png).

```
GitHub (monorepo)                                 GitHub (trading_maxxing-infra)
  feature/* ─PR─▶ develop ─PR─▶ main                Terraform cloudflare/cloudflare
        │             │             │                 envs/dev, envs/prod:
        ▼             ▼             ▼                 D1, Pages, Access (app web +
     CI (lint,   deploy dev     aprobación            service token del motor)
     pruebas,    automático     environment
     cobertura)  + UAT          `prod`
        │
        ├──▶ Worker tm-api-dev / tm-api  (migraciones de D1 + wrangler deploy)
        ├──▶ Pages tm-web-dev / tm-web   (wrangler pages deploy del export estático)
        └──▶ Docker Hub (agents, engine, forecast) ──▶ Watchtower en la Mac
```

### Backend — Cloudflare Workers + D1

- **Dos ambientes**: Worker `tm-api-dev` con D1 `tm-dev` y Worker `tm-api` con D1 `tm-prod`. Las bases las crea Terraform; el Worker lo publica el pipeline con `wrangler deploy --env <env>` después de `wrangler d1 migrations apply`.
- **Login**: Cloudflare Access (allowlist de correos + One-time PIN) delante del dominio de Pages. El Worker valida el JWT de Access (`aud` y JWKS del equipo) y busca el correo en `users`.
- **Un solo origen**: Pages reenvía `/api/*` al Worker por el **service binding `API`** (`env.API`), así la cookie de Access llega sin CORS.
- **Motor**: `/internal/*` lo protege una segunda app de Access con *service token* (headers `CF-Access-Client-Id` / `CF-Access-Client-Secret`) y, además, el Worker exige `X-Service-Token`.

### Motor — Mac de Carlo

- `docker compose` con las imágenes `trading-maxxing-{agents,engine,forecast}` de Docker Hub y `redis`. **Watchtower** baja la imagen nueva del canal (dev o prod) cuando el CI la publica y reinicia el servicio.
- Sin puertos abiertos ni túneles: solo conexiones salientes a Binance Spot Testnet y a la API.
- Por qué no corre en Workers (Pyodide sin paquetes nativos, sin procesos permanentes, 128 MB, 10 ms de CPU) y cómo se movería a **Cloudflare Containers** cuando haya presupuesto: [`cloudflare.md` §4 y §5](../arquitectura/cloudflare.md).

### Frontend — Cloudflare Pages

- Export estático de Next.js (ver [`decisiones.md`](decisiones.md) §8) en `tm-web-dev` (rama de producción `develop`) y `tm-web` (`main`). El deploy a dev es automático; el de prod espera la aprobación del *environment* `prod`.
- La web llama a `/api/*` relativo: no necesita `NEXT_PUBLIC_API_URL` ni llaves de Supabase.

### Observabilidad

- **Workers Observability** (activado en `wrangler.toml`): logs JSON por request con `request_id`, trazas e invocaciones con error. Para la demo de APM, `POST /api/admin/debug/error` como admin y buscar el `request_id` (issue [#29](https://github.com/CarloSantoro21/trading_maxxing/issues/29)).
- **Métricas**: el panel del Worker muestra requests, errores, CPU time y duración. Workers no reporta memoria; la del motor se ve con `docker stats` en la Mac (o por contenedor si migramos a Cloudflare Containers).
- **Salud del motor**: la web muestra "motor en línea / desconectado" según el último latido (`GET /api/engine/status`).

### Infraestructura como código — `trading_maxxing-infra`

- Repo separado: [`trading_maxxing-infra`](https://github.com/CarloSantoro21/trading_maxxing-infra) (issues [#26](https://github.com/CarloSantoro21/trading_maxxing/issues/26), [#39](https://github.com/CarloSantoro21/trading_maxxing/issues/39) y [#109](https://github.com/CarloSantoro21/trading_maxxing/issues/109)).
- Provider `cloudflare/cloudflare`, dos stacks (`envs/dev` y `envs/prod`) con D1, el proyecto de Pages y Access (app web y service token del motor).
- Outputs que consume este repo: `d1_database_id`, `pages_project_name`, `access_team_domain`, `access_web_aud`, `motor_service_token_client_id` / `motor_service_token_client_secret`.

## Secretos

Resumen; el detalle operativo está en [setup-local.md](setup-local.md) y en [`cloudflare.md` §8](../arquitectura/cloudflare.md).

- **Nunca en el repo.** `.env` y `apps/api/.dev.vars` están en `.gitignore`. Los archivos de referencia son `.env.example` y `apps/api/.dev.vars.example`.
- **GitHub Actions** (secrets por *environment* `dev` y `prod`, la "service connection" de la rúbrica): `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `DOCKERHUB_USERNAME` y `DOCKERHUB_TOKEN`. Sin ellos los workflows construyen y avisan en el *job summary*.
- **Worker**: `SERVICE_TOKEN` con `wrangler secret put SERVICE_TOKEN --env <env>`.
- **Mac**: `TM_API_URL`, `TM_SERVICE_TOKEN`, `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET`, llaves de Binance Testnet y, solo si se prende el LLM, las del proveedor.
- **Terraform**: variables `sensitive`; el secreto del service token sale como output sensible.

## Variables de entorno principales

Las tres piezas del worker `agents` se controlan con flags (todas corren por omisión):

| Variable | Default | Apaga con `0` | Necesita |
|---|---|---|---|
| `ORCHESTRATOR_SHARED` | `1` | capa compartida (publica `tm:signals`) | `REDIS_URL` |
| `ORCHESTRATOR_BOTS` | `1` | capa por usuario (`tm:signals` → `tm:decisions`) | `REDIS_URL`, `TM_API_URL`, `TM_SERVICE_TOKEN` |
| `REFERENCE_SERVICE` | `1` | ledger de referencia y `LifecycleMonitor` | `REDIS_URL`, `TM_API_URL`, `TM_SERVICE_TOKEN` |

Conexión con la API (los mismos nombres en agents y engine): `TM_API_URL` (base del Worker, sin `/internal`), `TM_SERVICE_TOKEN` (header `X-Service-Token`) y, opcionales, `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` (headers `CF-Access-Client-Id` / `CF-Access-Client-Secret`).

LLM y noticias, **apagados por defecto** (#118): `NEWS_ENABLED=false`, `EXPLAINER_ENABLED=false`, `LLM_PROVIDER=none`. Un admin los prende en caliente con `PUT /api/admin/settings` y el motor los lee de `/internal/settings`.

Lista completa en [`.env.example`](../../.env.example).

## Backtest vs. en vivo

Las mismas clases corren en los dos modos; solo cambian la fuente de velas y los streams:

| | En vivo | Backtest |
|---|---|---|
| Velas | `MarketDataBarsProvider` | `FrameBarsProvider` (DataFrames) |
| Estrategias | `StrategyRegistry` contra la API (`/internal/strategies`) | `StrategyRegistry` con `InMemoryBackend` |
| `tm:signals` | `RedisSignalSink` / `RedisSignalSource` | `run_offline` o `ListSignalSource` |
| `tm:decisions` | `RedisStreamSink` | `MemorySink` → `ScheduledDecisionSource` |
| Venue | TradingNode contra Binance Testnet | `BacktestEngine` con venue simulado |

`services/engine/tests/test_orchestrator_backtest.py` prueba de punta a punta que la posición de cada bot en el motor coincide con su libro virtual.
