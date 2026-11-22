# Contrato de la API (Cloudflare Workers + D1)

Issue #115. La API vive en `apps/api` (Hono + TypeScript). Reemplazó a la API de FastAPI y a
Supabase (Postgres, Auth, PostgREST, RLS y Realtime), que ya se borraron del repo.

```
Navegador ──(Cloudflare Access: OTP por correo)──▶ Pages (Next.js estático)
     │                                                │ fetch /api/* con la cookie de Access
     ▼                                                ▼
            Worker "tm-api-dev" / "tm-api"  ──▶  D1 "tm-dev" / "tm-prod"
                        ▲  /internal/* (X-Service-Token)
                        │  solo conexiones SALIENTES desde la Mac
         Mac de Carlo: agents · engine (NautilusTrader) · forecast (Kronos) · Redis
```

## 1. Convenciones

| Tema | Regla |
|---|---|
| Prefijos | `/api/*` para el frontend, `/internal/*` para el motor de la Mac. `/api/health` no pide auth. |
| Formato de error | Igual que FastAPI: `{"detail": "mensaje"}`. Validación: `422` con `{"detail": [{"type", "loc", "msg"}]}` (`loc` empieza con `body`, `query` o `path`). Algunos errores traen `code` (ver sección 5). |
| Error 500 | `{"detail": "Error interno del servidor", "request_id": "..."}`; el `request_id` también va en el header `X-Request-Id` y en los logs. |
| Ids | UUID en texto (`crypto.randomUUID()`). Un id mal formado en la ruta da 422. |
| Fechas | Fecha y hora en ISO 8601 UTC con milisegundos (`2026-11-21T10:00:00.000Z`). Fechas sin hora: `YYYY-MM-DD`. La API acepta cualquier ISO con zona (también los microsegundos de Python) y lo normaliza. |
| Números | Se devuelven como número JSON. En la entrada, los montos aceptan número o texto (`"10000"`), como `Decimal` en pydantic. |
| Campos extra | Los cuerpos de entrada son estrictos: un campo desconocido da 422 (igual que `extra="forbid"`). |
| CORS | Orígenes de `CORS_ORIGINS`, con `credentials: true` (la cookie `CF_Authorization` de Access). |

### Autenticación

| `AUTH_MODE` | Cómo se identifica al usuario |
|---|---|
| `access` (dev y prod en la nube) | JWT de Cloudflare Access en `Cf-Access-Jwt-Assertion` (o cookie `CF_Authorization`). Se valida firma (JWKS de `https://<ACCESS_TEAM_DOMAIN>/cdn-cgi/access/certs`), `aud = ACCESS_AUD`, `iss = https://<ACCESS_TEAM_DOMAIN>` y `exp`. Con el claim `email` se busca el usuario en `users`. |
| `dev` (solo `wrangler dev --local` y pruebas) | Header `X-Dev-User: <correo>` o la variable `DEV_USER_EMAIL`. Con `ENVIRONMENT=production` se rechaza. |

* Sin token o con token inválido: **401**. Correo válido pero no dado de alta en `users`: **403** (un admin lo da de alta con `POST /api/admin/users`; también hay que agregarlo a la allowlist de Access).
* Roles: `admin` o `member` (columna `users.role`). Las acciones de admin responden **403** a un member.
* `/internal/*`: header `X-Service-Token` igual al secreto `SERVICE_TOKEN`; o un JWT de Access emitido para un *service token* cuyo `common_name` (Client ID) esté en `ACCESS_SERVICE_CLIENT_IDS`.

### RLS → filtros por usuario

No hay RLS en D1. Cada consulta pública filtra por el usuario autenticado: `profiles.user_id`
directo, y `goals`, `bots`, `backtests`, `trades` y `equity_snapshots` con `JOIN profiles`.
Lo ajeno responde **404** (igual que con RLS, donde la fila "no existía"). `strategies`,
`strategy_transitions`, `reference_equity` y los reportes del laboratorio son compartidos.

## 2. Endpoints públicos (`/api`)

Los 34 endpoints de FastAPI conservan su contrato JSON (ruta = la de FastAPI con prefijo `/api`).
Marcados con ★ los nuevos.

### Usuario, ajustes y administración

| Método y ruta | Quién | Request | Response |
|---|---|---|---|
| `GET /api/health` | público | – | `{service:"api", status:"ok", version, environment, time}` |
| `GET /api/me` | usuario | – | `{id, email, role, profile: Profile \| null}` (★ `profile`) |
| ★ `GET /api/settings` | usuario | – | `{news_enabled: bool, explainer_enabled: bool}` |
| ★ `GET /api/admin/users` | admin | – | `[{id, email, role, created_at, updated_at}]` |
| `POST /api/admin/users` | admin | `{email, role?: "admin"\|"member"}` | `201 {id, email, role, created_at, updated_at}`; `409` si el correo existe. **Cambio:** ya no devuelve `temporary_password` (el login es con el OTP de Access). |
| ★ `PATCH /api/admin/users/{id}` | admin | `{role}` | usuario; `409` si te quitas el admin a ti mismo |
| ★ `DELETE /api/admin/users/{id}` | admin | – | `204`; `409` si te borras a ti mismo (borra en cascada su perfil) |
| ★ `GET /api/admin/settings` | admin | – | igual que `/api/settings` |
| ★ `PUT /api/admin/settings` | admin | `{news_enabled?: bool, explainer_enabled?: bool}` | ajustes completos; `422` con una llave desconocida |
| ★ `POST /api/admin/debug/error` | admin | – | `500` a propósito: deja un `console.error` estructurado y una excepción `DemoError` con stack (demo de APM, rúbrica) |

### Perfiles

`Profile = {id, user_id, horizon, risk_level, capital, max_drawdown, instruments, created_at, updated_at}`.
`max_drawdown` lo deriva la API de `risk_level` (1: 0.10, 2: 0.15, 3: 0.20, 4: 0.30, 5: 0.40).

| Método y ruta | Request | Response |
|---|---|---|
| `GET /api/profiles` | – | `Profile[]` |
| `GET /api/profiles/me` | – | `Profile`; `404` "Aún no tienes perfil…" |
| `POST /api/profiles` | `{risk_level: 1..5, capital: >= 500}` | `201 Profile`; `409` si ya tiene |
| `GET /api/profiles/{id}` | – | `Profile` |
| `PATCH /api/profiles/{id}` | `{risk_level?, capital?}` (al menos uno, sin `null`) | `Profile` |
| `DELETE /api/profiles/{id}` | – | `204` (cascada: metas, bots, backtests) |

### Metas

`Goal = {id, profile_id, target_return, deadline, created_at, updated_at}`.

| Método y ruta | Request | Response |
|---|---|---|
| `GET /api/goals?profile_id=` | – | `Goal[]` (por `deadline`) |
| `POST /api/goals` | `{profile_id, target_return > 0, deadline >= hoy}` | `201 Goal`; `404` perfil ajeno |
| `GET /api/goals/{id}` | – | `Goal` |
| `PATCH /api/goals/{id}` | `{target_return?, deadline?}` | `Goal` |
| `DELETE /api/goals/{id}` | – | `204` |
| `GET /api/goals/{id}/probability` | – | `GoalProbability` (mismos campos que FastAPI: `probability`, `probability_at_deadline`, `fan[{day,p10,p50,p90}]`, `strategies[{name,state,source,report_source,observations,weight,exposure,excluded}]`, `notes`, …); `422` si la fecha ya pasó o está a más de 10 años |

La proyección es un port del Monte Carlo #3 (block bootstrap de 20 días, semilla 42). Por el
límite de CPU del plan gratuito usa a lo más 1,000,000 celdas (simulaciones × días) y el
generador es mulberry32: la respuesta es determinista, pero no idéntica a la de numpy.

### Bots

`Bot = {id, profile_id, name, status, strategy_config, started_at, stopped_at, created_at, updated_at}`.
`BotCommandResult = {bot: Bot, command: "start"|"stop"|"kill", command_id}`.

| Método y ruta | Request | Response |
|---|---|---|
| `GET /api/bots?profile_id=&status=` | – | `Bot[]` |
| `POST /api/bots` | `{name, profile_id?, strategy_config?}` | `201 Bot` (queda `stopped`) |
| `POST /api/bots/kill-all` | `{reason?}` opcional | `{killed: BotCommandResult[]}` |
| `GET /api/bots/{id}` | – | `Bot` |
| `PATCH /api/bots/{id}` | `{name?, strategy_config?}` | `Bot` |
| `DELETE /api/bots/{id}` | – | `204`; `409` si está `running` o `paused` |
| `POST /api/bots/{id}/start` | – | `BotCommandResult`; `409` si ya corre |
| `POST /api/bots/{id}/stop` | – | `BotCommandResult`; `409` si no corre |
| `POST /api/bots/{id}/kill` | `{reason?}` opcional | `BotCommandResult` (payload `{close_positions: true}`) |

**Cambio:** el comando ya no va al stream `tm:commands` de Redis. El cambio de estado y el
comando se escriben juntos (batch atómico) en la tabla `commands`; `command_id` es su id. El
motor los lee con `GET /internal/commands`. Ya no existe el `503` por Redis caído.

### Portafolio, operaciones y backtests

| Método y ruta | Request | Response |
|---|---|---|
| `GET /api/portfolio?profile_id=` | – | `{profile_id, capital, max_drawdown, equity, cash, positions_value, realized_pnl, total_return, sleeves[], open_positions: Trade[]}` |
| `GET /api/trades?bot_id=&symbol=&state=open\|closed&limit=1..200&offset=` | – | `{items: Trade[], total, limit, offset}` |
| `GET /api/backtests?status=&limit=` | – | `Backtest[]` |
| `POST /api/backtests` | `{profile_id?, config: {symbols, timeframe?, start, end, mode?, strategies?, initial_capital?}}` | `202 Backtest` (`queued`) |
| `GET /api/backtests/{id}` | – | `Backtest` |

### Estrategias y laboratorio

`Strategy = {id, name, state, params, lab_report, live_metrics, state_changed_at, created_at, updated_at, shadow_days_clean}` (★ `shadow_days_clean`, antes columna calculada de PostgREST).

| Método y ruta | Quién | Request | Response |
|---|---|---|---|
| `GET /api/strategies` | usuario | – | `Strategy[]` |
| ★ `GET /api/strategies/overview` | usuario | – | `{strategies: Strategy[], transitions: Transition[] (500 más recientes), equity: ReferenceEquity[], isAdmin}` (reemplaza `lib/strategies/data.ts`) |
| ★ `GET /api/strategies/reference-equity?strategy_id=&limit=` | usuario | – | `ReferenceEquity[]` |
| `GET /api/strategies/{name}` | usuario | – | `Strategy` |
| ★ `GET /api/strategies/{name}/transitions` | usuario | – | `Transition[]` (del más viejo al más nuevo) |
| `POST /api/strategies/{name}/promote` | admin | `{reason}` | `{strategy, from_state, to_state: "active", actor, reason}`; `409` si no está en shadow/probation o lleva < 7 días en shadow |
| `POST /api/strategies/{name}/retire` | admin | `{reason}` | igual, `to_state: "retired"`; `409` si ya está retirada |
| `GET /api/lab/reports` | usuario | – | `LabReportSummary[]` |
| `GET /api/lab/reports/{name}` | usuario | – | `LabReportDetail` (con `drawdown_histogram`); `404` / `422` |

Los reportes del laboratorio se empaquetan en el Worker: `scripts/build-lab-reports.ts` lee
`docs/estrategias/reportes/*.json` en build y genera `src/generated/lab-reports.json`.

### Reemplazos de las lecturas directas a Supabase (★)

| Método y ruta | Reemplaza a | Response |
|---|---|---|
| `GET /api/dashboard` | `lib/dashboard/data.ts` (`fetchDashboard`) | `DashboardData`: `{profile: {id, capital, risk_level, max_drawdown} \| null, bots, snapshots (asc, máx. 2000), recentTrades (20), openTrades, closed: {[trade_id]: {sleeve, pnl}}}` |
| `GET /api/equity-snapshots?bot_id=&since=&limit=` | Realtime de `equity_snapshots` | fotos (asc) más nuevas que `since`, para polling |
| `GET /api/decisions?bot_id=&limit=` | – | decisiones del orquestador de los bots del usuario |
| `GET /api/onboarding` | `lib/onboarding/repository.ts` (`fetchProfile`) | `{id, risk_level, capital, max_drawdown, goals: [{target_return, deadline}]}` o `null` |
| `PUT /api/onboarding` | `saveOnboarding` | request `{risk_level, capital, goals: [{target_return, deadline}]}`; crea o actualiza el perfil y **reemplaza** las metas en un batch atómico; response `{profileId, profile_id}` |
| `GET /api/engine/status` | – | `{online, last_heartbeat_at, stale_after_seconds, components: [{component, status, version, details, host, started_at, last_seen_at, age_seconds, online}]}` |

Realtime desaparece: el frontend refresca con polling (`/api/dashboard` o `/api/equity-snapshots?since=`).

## 3. Endpoints internos (`/internal`, para el motor de la Mac)

Pensados para un `WorkerApiBackend` en Python que sustituye a los backends `PostgREST*`.

| Método y ruta | Reemplaza a | Request | Response |
|---|---|---|---|
| `GET /internal/strategies?state=active,probation` | `PostgRESTBackend.list` | – | `Strategy[]` (por nombre) |
| `GET /internal/strategies/{name}` | `.get` | – | `Strategy`; `404 not_found` |
| `POST /internal/strategies` | `.create` | `{name, params?}` | `201 Strategy` (`research`); `409 strategy_exists` |
| `PATCH /internal/strategies/{name}` | `.update` (`set_params`, `record_lab_report`, `record_live_metrics`) | `{params?, lab_report?, live_metrics?}` | `Strategy` |
| `GET /internal/strategies/{name}/transitions` | `.history` | – | `[{id, strategy, strategy_id, from_state, to_state, actor, reason, created_at}]` |
| `POST /internal/strategies/{name}/transitions` | `.append_transition` | `{from_state, to_state, actor: "system"\|uuid, reason}` | `201` transición; `409 stale_state` si el estado ya cambió; `409 transition_not_allowed` |
| `GET /internal/bots?status=running` | `PostgRESTBotRepository.running` | – | `[{id, name, status, strategy_config, profile_id, user_id, profile: {risk_level, capital, max_drawdown, horizon, instruments}}]` |
| `GET /internal/bots/{id}` | `.get` | – | igual, uno; `404` |
| `GET /internal/trades?bot_id=&open=true\|false&side=buy&limit=..1000&offset=` | `PostgRESTTradeStateRepository.rows`, `SupabaseTradeRecorder.open_positions` | – | `Trade[]` (por `opened_at, id` asc) |
| `POST /internal/trades` | `SupabaseTradeRecorder._open_trade` | `{bot_id, symbol, side, quantity, entry_price, stop_loss?, take_profit?, fees?, rationale?, opened_at?}` | `201 Trade` |
| `PATCH /internal/trades/{id}` | `SupabaseTradeRecorder._patch` | `{quantity?, entry_price?, fees?, rationale?, exit_price?, closed_at?, pnl?, stop_loss?, take_profit?}` | `Trade` |
| `POST /internal/equity-snapshots` | – (nuevo) | `{snapshots: [{bot_id, ts, equity, cash, positions_value?, drawdown?}]}` (máx. 500) | `{upserted}` (upsert por `bot_id, ts`) |
| `POST /internal/reference/trades` | `PostgRESTReferenceStore.save_trades` | `{rows: [...]}` (las mismas filas de `trade_row`) | `{received, inserted}` (ignora duplicados) |
| `PUT /internal/reference/equity` | `.upsert_equity` | `{points: [EquityPoint.to_row()]}` | `{upserted}` (upsert por `strategy_id, day`) |
| `GET /internal/reference/equity?strategy_id=` | – | – | puntos del ledger |
| `GET /internal/commands?after=<seq>&limit=` | `CommandReader` (stream `tm:commands`) | – | `[{seq, id, type, bot_id, user_id, reason, payload, issued_at}]` pendientes, por `seq` |
| `POST /internal/commands/{id}/ack` | `XACK` | `{status?: "done"\|"failed", error?}` | comando confirmado; ya no aparece en la lista |
| `GET /internal/backtests?status=queued&limit=` | stream `tm:backtests` | – | `Backtest[]` con `user_id` |
| `PATCH /internal/backtests/{id}` | – | `{status?, report?, error?, started_at?, finished_at?}` | `Backtest`; `422 check_violation` si `done` sin reporte o `failed` sin error |
| `POST /internal/decisions` | `DecisionSink` (auditoría) | `{decisions: [{bot_id?, strategy?, symbol?, kind, payload?, decided_at?}]}` | `201 {inserted}` |
| `GET /internal/state/{key}` / `PUT /internal/state/{key}` | estado del orquestador | `PUT {value: any}` | `{key, value, updated_at}` |
| `POST /internal/heartbeat` | – (nuevo) | `{component, status?: ok\|degraded\|error\|stopping, version?, host?, started_at?, details?}` | latido guardado (upsert por componente) |
| `GET /internal/settings` | – | – | `{news_enabled, explainer_enabled}` (el motor apaga noticias y explainer si están en false) |

Flujo sugerido del motor: latido cada 30 s; `GET /internal/commands?after=<último seq>` cada
pocos segundos y `ack` de cada uno; `GET /internal/backtests?status=queued` cada minuto.

## 4. Base de datos (D1)

Migraciones en `apps/api/migrations/` (port de las 9 de `supabase/migrations`):

| Migración | Contenido |
|---|---|
| `0001_core.sql` | `users`, `profiles`, `goals`, `bots`, `trades`, `equity_snapshots`, `backtests` |
| `0002_strategies.sql` | `strategies`, `strategy_transitions` y los triggers del ciclo de vida |
| `0003_reference_ledger.sql` | `reference_trades`, `reference_equity` |
| `0004_engine.sql` | `settings` (con `news_enabled` y `explainer_enabled` en false), `engine_heartbeats`, `commands`, `decisions`, `orchestrator_state` |

Reglas del port: enums → `CHECK (col IN (...))`; `uuid` → `TEXT`; `timestamptz` → `TEXT`
ISO UTC; `jsonb`/`text[]` → `TEXT` con `json_valid`; RLS → filtros en la API. Los triggers
de `strategy_transitions` siguen en la base (SQLite): rechazan una transición con estado
viejo (`stale_state`) y actualizan `strategies.state`.

## 5. Códigos de error para clientes

| `code` | HTTP | Significado |
|---|---|---|
| `not_found` | 404 | (internos) el recurso no existe |
| `strategy_exists` | 409 | ya hay una estrategia con ese nombre |
| `stale_state` | 409 | la estrategia cambió de estado entre la lectura y la transición |
| `transition_not_allowed` | 409 | la transición no está permitida (o promoción sin persona) |
| `unique_violation` | 409 | registro duplicado |
| `check_violation` | 422 | los datos rompen un `CHECK` de la base |
| `foreign_key_violation` | 422 | el registro referenciado no existe |

## 6. Observabilidad

* `wrangler.toml` activa `observability` (logs y trazas) en los dos ambientes.
* Cada petición escribe un log JSON: `{level, msg: "request", request_id, method, path, route, status, ms, user, service}`.
* Las excepciones no controladas se loguean con `error_name`, `error_message` y `stack`, y la respuesta lleva el `request_id`.
* Para la demo de APM: `POST /api/admin/debug/error` como admin y buscar el `request_id` en Workers Observability.
