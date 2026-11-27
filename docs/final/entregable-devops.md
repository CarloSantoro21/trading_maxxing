# Entregable final — Infraestructura para el Desarrollo Continuo (ITESO)

> Proyecto: **trading_maxxing** — plataforma multiestrategia de trading de criptomonedas (paper trading, Binance Spot Testnet) desplegada íntegramente en Cloudflare, con dos ambientes (`dev` y `prod`), CI/CD de extremo a extremo e infraestructura declarativa en Terraform.

## 1. Resumen ejecutivo

- **Repo del monorepo:** https://github.com/camar-on/trading_maxxing
- **Repo de infraestructura (Terraform):** https://github.com/camar-on/trading_maxxing-infra
- **Dev (público, detrás de Cloudflare Access):** https://tm-web-dev.pages.dev
- **Prod (público, detrás de Cloudflare Access):** URL asignada por Cloudflare al crear el proyecto de Pages (`tm-web-<hash>.pages.dev`); se descubre en el pipeline y se imprime en el summary del deploy
- **Costo:** 0 USD/mes (plan gratuito de Cloudflare)
- **Herramientas de gestión:** GitHub Issues + Project Board (sustituye a Azure DevOps; la rúbrica permite GitHub)
- **Wiki:** https://github.com/camar-on/trading_maxxing/wiki (fuente en [`docs/wiki/`](../wiki/))

### Mapa rúbrica → sección

| Criterio de la rúbrica | Sección |
|---|---|
| Descripción del proyecto | §2 |
| Descripción de la tecnología | §3 |
| Componentes y métodos (clases) | §4 |
| Diagrama de infraestructura | §5 |
| Diagrama de deployment | §6 |
| Estrategia de ramas | §7 |
| Repositorio de Infra con Terraform | §8 |
| Pipelines backend (build, pruebas, dev, prod) | §9 |
| Pipelines frontend (dev, prod) | §10 |
| Docker Hub + service connection | §11 |
| Recursos creados en Cloudflare (equivalente a Azure Portal) | §12 |
| APM: trazas, errores, mensajes | §13 |
| Métricas (CPU, memoria, requests) | §14 |
| Guion de la demo (cambio → PR → dev → prod) | §15 |
| Entregables y URLs | §16 |

---

## 2. Descripción del proyecto

**trading_maxxing** es una plataforma web que permite a un usuario autenticado definir un perfil de riesgo, metas de rendimiento y operar un bot de criptomonedas **en modo paper trading** sobre Binance Spot Testnet. Un comité de agentes combina el pronóstico de un modelo de series temporales ([Kronos](https://github.com/shiyu-coder/Kronos)) con estrategias cuantitativas con evidencia publicada (trend following, momentum intradía), simula cada decisión con Monte Carlo, aplica una capa de gestión de riesgo y, si pasa, ejecuta la orden en el broker.

**Problema que resuelve:** demostrar un stack DevOps completo (CI/CD, IaC, APM, dos ambientes, gates manuales, observabilidad) sobre un producto real y no un "hola mundo". La lógica de trading es el pretexto; la plataforma DevOps es el entregable.

**Usuarios:** miembros del equipo con correo en la allowlist. El login se hace con Cloudflare Access (One-time PIN por correo). Hay dos roles: `admin` (puede dar de alta usuarios) y `member` (opera su propio bot).

**Alcance del MVP:**
- Dashboard con estado del bot, operaciones abiertas, PnL y métricas
- Onboarding de perfil (riesgo, capital, horizonte)
- Visualización de estrategias y backtests
- Gestión de metas y bots
- Panel de admin para alta de usuarios
- Motor local en Mac que ejecuta el flujo completo contra Binance Testnet (opcional; sin él la app funciona pero no hay señales)

---

## 3. Stack tecnológico

| Capa | Tecnología | Versión | Por qué |
|---|---|---|---|
| **Frontend** | Next.js (static export) + React | 15.x + 19.x | Export estático = Cloudflare Pages barato y rápido; SSR no es necesario para una app con auth |
| | TypeScript | 5.x | Tipado estricto en todo el repo |
| | Tailwind CSS | 4.x | Diseño sin CSS ad hoc, dark mode out of the box |
| | SWR | 2.x | Cache + revalidación para GET a la API |
| | Playwright | 1.x | E2E mocked (en CI) + UAT contra dev (post-deploy) |
| | Vitest | 3.x | Unit tests, JUnit + cobertura v8 |
| **API** | Cloudflare Workers + Hono | wrangler 4 + Hono 4 | Edge runtime, 0 USD en free tier, latencia baja global |
| | TypeScript | 5.x | Mismo monorepo |
| | D1 (SQLite serverless) | — | Base del plan free de Cloudflare; migraciones con `wrangler d1 migrations` |
| **Auth** | Cloudflare Access (Zero Trust) | One-time PIN | Login sin contraseña vía correo; service tokens para el motor y el pipeline |
| **Motor** | Python 3.12 + NautilusTrader | 1.x | Framework open source de trading algorítmico |
| | FastAPI + uvicorn | — | Endpoints internos del motor |
| | Redis | 7 | Cola entre agents y engine |
| | Docker Compose + Watchtower | — | Deploy continuo en la Mac desde Docker Hub |
| **IaC** | Terraform | 1.9.8 | Declarativo, state remoto en HCP Terraform |
| | Cloudflare provider | 5.x | Workers + Pages + D1 + Access desde Terraform |
| **CI/CD** | GitHub Actions | — | Nativo al repo; 3 workflows (backend, frontend, terraform) |
| **State backend** | HCP Terraform (Terraform Cloud) | — | State remoto, bloqueos, local execution mode |
| **Registry** | Docker Hub (opcional, solo para el motor) | — | Watchtower en la Mac lo poolea |
| **APM** | Cloudflare Workers Observability | — | Logs + traces + invocaciones, nativo, gratis |
| **LLM (opcional, apagado)** | Claude / OpenAI / Together | — | Análisis de noticias; flag `NEWS_ENABLED=0` por defecto |

---

## 4. Componentes y métodos

### Vista de módulos (equivalente a diagrama de clases)

```
                      ┌─────────────────────────────────────┐
                      │          apps/web (Next.js)         │
                      │                                     │
                      │  AppShell  ← layout con auth        │
                      │  RequireAuth  ← Cloudflare Access   │
                      │  DashboardView, StrategiesView,     │
                      │    GoalsView, OnboardingLoader,     │
                      │    AdminView, BotControls           │
                      │  useMe(), useEngineStatus()         │
                      │  mockApi(), renderWithApi()         │
                      └─────────────────┬───────────────────┘
                                        │ fetch /api/*
                                        ▼
            ┌───────────────────────────────────────────────┐
            │     apps/api (Worker + Hono)                  │
            │                                               │
            │  Middlewares:                                 │
            │    requireUser   ← JWT Access + tabla users   │
            │    requireService ← X-Service-Token           │
            │    requireAdmin  ← rol admin en D1            │
            │                                               │
            │  Rutas:                                       │
            │    /api/me, /api/health, /api/bots            │
            │    /api/goals, /api/strategies, /api/trades   │
            │    /api/engine/status, /api/admin/users       │
            │    /internal/decisions, /internal/heartbeat   │
            │                                               │
            │  Modelo D1:                                   │
            │    users(id, email, role)                     │
            │    profiles(user_id, risk_level, capital)     │
            │    goals(profile_id, target_return, deadline) │
            │    bots(profile_id, status, strategy_config)  │
            │    trades(bot_id, side, qty, pnl, rationale)  │
            │    strategies, ledger, settings               │
            └─────────────────┬─────────────────────────────┘
                              │ /internal/* con X-Service-Token
                              ▼
┌──────────────────────────────────────────────────────────────┐
│          Motor (Mac, docker-compose.runner.yml)              │
│                                                              │
│  services/agents    kronos | trend | intraday_momentum       │
│                     news_veto | risk_manager                 │
│  services/engine    NautilusTrader → Binance Testnet         │
│  services/forecast  Kronos forecaster (contenedor GPU/CPU)   │
│  packages/shared    WorkerApiBackend, api_client, logging    │
│  Redis              cola tm:signals → tm:decisions           │
│  Watchtower         pull desde Docker Hub                    │
└──────────────────────────────────────────────────────────────┘
```

### Métodos relevantes (puntos de entrada)

| Clase / módulo | Métodos | Qué hace |
|---|---|---|
| `AppShell` | `EnvBadge`, `EngineIndicator`, `AppShell({children})` | Encabezado con nav, estado del motor, badge de ambiente y logout |
| `RequireAuth` | `useMe()`, `LOGOUT_URL` | Wrap de páginas con sesión; maneja 401/403 y redirige a la pantalla de acceso |
| `requireUser` (api) | JWT → `findUserByEmail(db, email)` | Valida token de Access y busca al usuario en D1 |
| `requireService` (api) | `safeEqual(sent, SERVICE_TOKEN)` | Valida header `X-Service-Token` con compare en tiempo constante |
| `WorkerApiBackend` (shared) | `post_decision`, `heartbeat`, `get_bots` | Cliente HTTP del motor hacia `/internal/*` de la API |
| `orchestrator` (agents) | `collect_signals`, `weighted_decision`, `apply_risk` | Combina señales del comité y arma la decisión final |
| `engine_runner` (engine) | `on_bar`, `submit_bracket`, `on_fill` | Consume decisiones de Redis y ejecuta en Binance Testnet |

---

## 5. Diagrama de infraestructura (Cloudflare)

```
                            Internet
                               │
              ┌────────────────┴─────────────────┐
              ▼                                  ▼
  ╔═══════════════════════════════════════════════════════════╗
  ║         Cloudflare Access (Zero Trust)                    ║
  ║   IdP: One-time PIN (OTP por correo)                      ║
  ║   Allowlist: ACCESS_ALLOWED_EMAILS                        ║
  ║   Service token: tm-motor-{dev,prod} (para el motor y CI) ║
  ╚═══════════════════════════════════════════════════════════╝
              │                                  │
              ▼                                  ▼
   ┌──────────────────────┐          ┌──────────────────────┐
   │  Pages: tm-web-dev   │          │   Pages: tm-web      │
   │  (develop)           │          │   (main)             │
   │                      │          │                      │
   │  Next.js export      │          │  Next.js export      │
   │  + Pages Functions   │          │  + Pages Functions   │
   │  (service binding)   │          │  (service binding)   │
   └──────────┬───────────┘          └──────────┬───────────┘
              │  /api/*                         │  /api/*
              ▼                                 ▼
   ┌──────────────────────┐          ┌──────────────────────┐
   │ Worker: tm-api-dev   │          │   Worker: tm-api     │
   │ (develop)            │          │   (main)             │
   │                      │          │                      │
   │  Hono + TypeScript   │          │  Hono + TypeScript   │
   │  Workers Observability│         │  Workers Observability│
   └──────────┬───────────┘          └──────────┬───────────┘
              │                                 │
              ▼                                 ▼
   ┌──────────────────────┐          ┌──────────────────────┐
   │     D1: tm-dev       │          │     D1: tm-prod      │
   │  (SQLite serverless) │          │  (SQLite serverless) │
   └──────────────────────┘          └──────────────────────┘

                   ▲                                 ▲
                   │  HTTPS saliente                 │
                   │  /internal/* con:               │
                   │    CF-Access-Client-Id          │
                   │    CF-Access-Client-Secret      │
                   │    X-Service-Token              │
                   │                                 │
    ┌──────────────┴─────────────────────────────────┴─────────────┐
    │          Mac del equipo  (docker-compose.runner.yml)          │
    │                                                               │
    │   agents · engine (NautilusTrader) · forecast · redis         │
    │   watchtower → polling Docker Hub                             │
    │                                                               │
    │   → saliente a Binance Spot Testnet (api.binance.com)         │
    └───────────────────────────────────────────────────────────────┘
```

**Observabilidad:** cada Worker tiene `[observability]`, `[observability.logs]` y `[observability.traces]` activados en `wrangler.toml`; Cloudflare captura logs, trazas e invocaciones automáticamente. Ver §13.

---

## 6. Diagrama de deployment

```
   Repo infra (Terraform)                Repo app (monorepo)
   trading_maxxing-infra                 trading_maxxing
   ──────────────────────                ─────────────────
         │                                     │
   PR a develop                          PR a develop
         │                                     │
         ▼                                     ▼
   ┌──────────────┐                     ┌──────────────┐
   │  CI infra:   │                     │ CI monorepo: │
   │   fmt        │                     │  lint + tsc  │
   │   validate   │                     │  vitest      │
   │   test       │                     │  build       │
   │   tflint     │                     │  e2e-mocked  │
   │   plan dev   │                     │  (Playwright)│
   │   plan prod  │                     │  Docker build│
   └──────┬───────┘                     └──────┬───────┘
          │ merge                              │ merge
          ▼                                    ▼
      develop                              develop
          │                                    │
          │  workflow_dispatch                 │  push a develop
          │  (manual apply dev)                │  (automático)
          ▼                                    ▼
   ┌──────────────┐                     ┌──────────────────┐
   │ terraform    │                     │ deploy-dev:      │
   │  apply dev   │                     │   Workers tm-api-dev
   │  (cambios en │                     │   D1 migrate tm-dev
   │  Cloudflare) │                     │   Pages tm-web-dev
   └──────────────┘                     │   Docker push dev
                                        │   Smoke + UAT     │
                                        └────────┬─────────┘
                                                 │
                                           UAT verde
                                                 │
                                 PR develop → main (promote)
                                                 │
                                                 ▼
                                             main
                                                 │
                                           push a main
                                                 │
                                                 ▼
                                        ┌──────────────────┐
                                        │ uat-gate         │
                                        │  (lee checks)    │
                                        └────────┬─────────┘
                                                 │ ✅
                                                 ▼
                                        ┌──────────────────┐
                                        │ deploy-prod      │
                                        │  ⏸  espera        │
                                        │   required       │
                                        │   reviewer       │
                                        │  (environment    │
                                        │   `prod`)        │
                                        └────────┬─────────┘
                                                 │ aprobado
                                                 ▼
                                        ┌──────────────────┐
                                        │ Workers tm-api   │
                                        │ D1 migrate tm-prod│
                                        │ Pages tm-web      │
                                        │ Docker push prod │
                                        │ Smoke             │
                                        └──────────────────┘
```

**Gate manual en prod:** el environment `prod` de GitHub tiene **Required reviewers** activado (ver `Settings → Environments → prod`), así el deploy pausa hasta que alguien con permiso lo aprueba.

---

## 7. Repositorios y estrategia de ramas

### Dos repos

| Repo | Contenido | URL |
|---|---|---|
| **trading_maxxing** (monorepo) | Frontend, API, servicios Python, pipelines de app, docker-compose | https://github.com/camar-on/trading_maxxing |
| **trading_maxxing-infra** | Módulos y envs de Terraform, pipeline de IaC | https://github.com/camar-on/trading_maxxing-infra |

### Estrategia de ramas (trunk-based con dos troncos)

- `main` → prod. Protegida: PRs obligatorios, status checks obligatorios, 1 reviewer mínimo.
- `develop` → dev. Protegida: PRs obligatorios, status checks obligatorios.
- `feature/*`, `fix/*`, `docs/*`, `chore/*` → ramas cortas que salen de `develop` y vuelven con PR.
- Promoción a prod = PR `develop → main`. Cada merge a `main` dispara el pipeline de prod con su gate manual.

```
feature/add-logo ─┐
fix/pages-subdomain ──┤
docs/entregable  ─────┤───▶  develop  ──(PR)─▶  main
chore/bump       ─────┘        │                │
                               │                │
                       push → deploy dev       push → gate + deploy prod
```

### Ejemplos de PRs del proyecto (muestra la estrategia)

| PR | Rama | Objetivo | Estado |
|---|---|---|---|
| [app #136](https://github.com/camar-on/trading_maxxing/pull/136) | `feat/runtime-cf-discovery` | Eliminar variables de GitHub, descubrir recursos de Cloudflare en runtime | ✅ Merged |
| [app #139](https://github.com/camar-on/trading_maxxing/pull/139) | `fix/uat-discover-cwd` | Fix cwd del step de discover en uat.yml | ✅ Merged |
| [app #140](https://github.com/camar-on/trading_maxxing/pull/140) | `fix/smoke-real-pages-subdomain` | Smoke test apunta al subdominio real de Pages | ✅ Merged |
| [app #144](https://github.com/camar-on/trading_maxxing/pull/144) | `feat/add-logo` | Logo en header y READMEs | ✅ Merged |
| [app #146](https://github.com/camar-on/trading_maxxing/pull/146) | `feat/polish-header` | Pulida visual del header | 🟡 Open |
| [infra #3](https://github.com/camar-on/trading_maxxing-infra/pull/3) | `feat/runtime-cf-discovery` | Discover de access_team_name y workers_dev_subdomain | ✅ Merged |
| [infra #5](https://github.com/camar-on/trading_maxxing-infra/pull/5) | `fix/pages-subdomain-from-resource` | Leer subdominio real de Pages desde el resource | ✅ Merged |
| [infra #7](https://github.com/camar-on/trading_maxxing-infra/pull/7) | `fix/web-access-service-token` | Aceptar service token en la app web de Access | ✅ Merged |
| [infra #9](https://github.com/camar-on/trading_maxxing-infra/pull/9) | `feat/add-logo` | Logo en el README | ✅ Merged |

---

## 8. Repositorio de infraestructura (Terraform)

**Repo:** https://github.com/camar-on/trading_maxxing-infra

### Estructura

```
trading_maxxing-infra/
├── envs/
│   ├── dev/        ← workspace HCP "trading-maxxing-dev"
│   │   ├── main.tf, variables.tf, outputs.tf, versions.tf
│   └── prod/       ← workspace HCP "trading-maxxing-prod"
│       └── ...
├── modules/
│   ├── access/     ← Zero Trust: IdP OTP, apps web y api, policies, service token
│   ├── d1/         ← Base D1 por ambiente
│   ├── pages/      ← Proyecto de Pages con build config y service binding a la API
│   └── worker_settings/ ← Nombre del Worker (el deploy real lo hace wrangler)
├── .github/workflows/terraform.yml
└── README.md
```

### State remoto

HCP Terraform (Terraform Cloud), dos workspaces en modo **Local execution**. El pipeline usa `TF_API_TOKEN` para autenticarse contra HCP y sube/baja state entre runs.

### Variables descubiertas en runtime

Para no tener que mantener variables de GitHub con valores que viven en Cloudflare, el pipeline consulta la API de Cloudflare al inicio de cada job y exporta:

- `access_team_name` ← `/accounts/:id/access/organizations`
- `workers_dev_subdomain` ← `/accounts/:id/workers/subdomain`

Esto reduce el inventario de secrets a 4: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `TF_API_TOKEN`, `ACCESS_ALLOWED_EMAILS`.

### Recursos que crea

Ver §12 para la lista completa por ambiente.

---

## 9. Pipelines del backend

**Workflow:** [`.github/workflows/backend.yml`](../../.github/workflows/backend.yml)

### Triggers y jobs

| Evento | Jobs que corren |
|---|---|
| PR a `develop` o `main` | `api` (tsc + vitest + JUnit + cobertura), `python` (ruff + pytest), `docker` (build sin push, matriz agents/engine/forecast) |
| Push a `develop` | Todo lo anterior + `docker` push (sha-xxx, dev) + `deploy-api-dev` + smoke + `uat-dev` |
| Push a `main` | Todo lo anterior + `docker` push (sha-xxx, prod) + `uat-gate` + `deploy-api-prod` (con `environment: prod` → gate manual) |

### Job `api` (Cloudflare Workers)

```
pnpm install --frozen-lockfile
pnpm typecheck
pnpm test  (vitest --reporter=junit --coverage)
pnpm build  (esbuild)
→ sube JUnit + cobertura como artifact
→ publica resumen de cobertura en $GITHUB_STEP_SUMMARY
```

### Job `python` (motor)

```
ruff check services/ packages/
pytest services/ packages/ --cov
```

### Job `docker` (matriz 3 servicios)

```
PR:           build amd64 sin publicar
push dev/prod: build multi-arch (amd64+arm64) y push a Docker Hub
Imágenes: <DOCKERHUB_USER>/trading-maxxing-{agents,engine,forecast}:{sha-xxx,dev|prod}
```

### Jobs de deploy (dev y prod)

Comparten anchors YAML (`&creds`, `&discover`, `&migrate`, `&deploy`, `&smoke`). Deploy = `wrangler deploy --env <env>` + `wrangler d1 migrations apply`.

Prod usa `environment: prod` → GitHub pausa hasta que un **required reviewer** aprueba.

### Resultados visibles

- **Pestaña Checks del PR:** reporte JUnit con cada test (pasa/falla)
- **Summary del run:** tabla de cobertura, lista de archivos publicados por `wrangler deploy`
- **Artifacts:** `api-coverage-y-junit`, `python-coverage-y-junit`

---

## 10. Pipelines del frontend

**Workflow:** [`.github/workflows/frontend.yml`](../../.github/workflows/frontend.yml)

### Triggers y jobs

| Evento | Jobs que corren |
|---|---|
| PR a `develop` o `main` | `web` (eslint + tsc + vitest + build + check:export), `e2e-mocked` (Playwright contra la app con API mockeada) |
| Push a `develop` | Todo lo anterior + `deploy-dev` (Pages tm-web-dev) + smoke + `uat-dev` (Playwright contra el deploy real) |
| Push a `main` | Todo lo anterior + `uat-gate` + `deploy-prod` (con `environment: prod` → gate manual) |

### Job `web`

```
pnpm lint                                 (eslint next/core-web-vitals)
pnpm typecheck                            (tsc --noEmit)
pnpm test  (vitest --reporter=junit --coverage con V8)
pnpm build (next build → export estático en apps/web/out)
pnpm check:export                         (valida las rutas exportadas)
```

### Job `e2e-mocked`

Playwright levanta `next dev` con la API totalmente mockeada (sin wrangler ni Access). Prueba el flujo completo en cada PR.

### Job `deploy-dev`

```
wrangler pages deploy out --project-name tm-web-dev --branch develop
→ consulta la API de Cloudflare para obtener el subdominio real
→ smoke test: GET /, GET /api/health con headers de service token
```

### Job `uat-dev` (reutilizable)

Llama a [`uat.yml`](../../.github/workflows/uat.yml) → corre Playwright contra la URL de dev con el service token de Access. Si falla, el check queda rojo y el gate de prod lo lee.

### Job `uat-gate` (en push a main)

Workflow [`uat-gate.yml`](../../.github/workflows/uat-gate.yml) busca el check "UAT (Playwright)" del mismo commit. Si está en failure o pending, falla y bloquea `deploy-prod`.

---

## 11. Docker Hub y service connections

### Docker Hub

- **Imágenes publicadas** (3, por servicio):
  - `<DOCKERHUB_USER>/trading-maxxing-agents:{sha-xxx, dev, prod}`
  - `<DOCKERHUB_USER>/trading-maxxing-engine:{sha-xxx, dev, prod}`
  - `<DOCKERHUB_USER>/trading-maxxing-forecast:{sha-xxx, dev, prod}`
- **Dónde se consumen:** `docker-compose.runner.yml` en la Mac, con `watchtower` polling cada 60 s.
- **Secrets necesarios (opcionales):** `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`. Si no existen, el job marca "Docker Hub omitido" con un warning (no falla).

### "Service connections" (secrets)

El equivalente a la service connection de Azure DevOps es el conjunto de secrets de GitHub por ambiente. Diseño: minimizar el inventario.

**Repo app `trading_maxxing`:**

| Nivel | Secret | Para qué |
|---|---|---|
| repo | `CLOUDFLARE_API_TOKEN` | Discover en CI (Pages, Workers, Access, D1) |
| repo | `CLOUDFLARE_ACCOUNT_ID` | Mismo |
| repo (opcional) | `DOCKERHUB_USERNAME` | Push de imágenes |
| repo (opcional) | `DOCKERHUB_TOKEN` | — |
| env `dev` | `CF_ACCESS_CLIENT_SECRET` | Service token para smoke/UAT contra dev |
| env `dev` | `TM_SERVICE_TOKEN` | Secret shared con el Worker (`SERVICE_TOKEN`); lo instala `wrangler secret put` |
| env `prod` | `CF_ACCESS_CLIENT_SECRET` | Idem, diferente valor |
| env `prod` | `TM_SERVICE_TOKEN` | Idem, diferente valor |

**Repo infra `trading_maxxing-infra`:**

| Nivel | Secret / variable | Para qué |
|---|---|---|
| repo | `CLOUDFLARE_API_TOKEN` | Terraform |
| repo | `CLOUDFLARE_ACCOUNT_ID` | — |
| repo | `TF_API_TOKEN` | HCP Terraform |
| repo | `ACCESS_ALLOWED_EMAILS` | Lista JSON: `["a@iteso.mx","b@iteso.mx"]` |
| repo var | `TF_CLOUD_ORGANIZATION` | Nombre de la org en HCP |
| env `dev` y `prod` | mismos 4 secrets | Scope por ambiente para `apply` |

**NO son necesarios** (se descubren en runtime vía API de Cloudflare):
- `WEB_URL`, `WORKERS_DEV_SUBDOMAIN`, `ACCESS_TEAM_NAME`, `ACCESS_TEAM_DOMAIN`, `ACCESS_AUD`, `D1_DATABASE_ID`, `CF_ACCESS_CLIENT_ID`

---

## 12. Recursos creados en Cloudflare

Equivalente al "Azure Portal" de la rúbrica. Todo lo de abajo lo crea Terraform.

| Recurso | Tipo TF | Dev | Prod |
|---|---|---|---|
| D1 database | `cloudflare_d1_database` | `tm-dev` | `tm-prod` |
| Worker | `cloudflare_worker` | `tm-api-dev` | `tm-api` |
| Pages project | `cloudflare_pages_project` | `tm-web-dev` | `tm-web` (subdominio real con sufijo: `tm-web-<hash>.pages.dev`) |
| Access app (web) | `cloudflare_zero_trust_access_application` | "trading_maxxing web (dev)" | "trading_maxxing web (prod)" |
| Access app (api) | idem | "trading_maxxing api (dev)" | "trading_maxxing api (prod)" |
| Access policy (team) | `cloudflare_zero_trust_access_policy` | "tm-dev allow equipo" | "tm-prod allow equipo" |
| Access policy (motor) | idem | "tm-dev service token motor" | "tm-prod service token motor" |
| Service token | `cloudflare_zero_trust_access_service_token` | `tm-motor-dev` | `tm-motor-prod` |
| IdP One-time PIN | `cloudflare_zero_trust_access_identity_provider` | (lo crea dev) | (lo reusa prod vía data source) |
| Service binding | dentro del Pages project | web → `tm-api-dev` | web → `tm-api` |

**Secrets del Worker** (se instalan con `wrangler secret put` durante el deploy):
- `SERVICE_TOKEN` ← viene de `TM_SERVICE_TOKEN` en GitHub env

**Variables del Worker** (en `wrangler.toml`, se sobrescriben en deploy):
- `ACCESS_TEAM_DOMAIN`, `ACCESS_AUD`, `D1_DATABASE_ID` (descubiertos en runtime)

---

## 13. APM — Cloudflare Workers Observability

### Configuración en `wrangler.toml` (ya activa)

```toml
[observability]
enabled = true
head_sampling_rate = 1

[observability.logs]
enabled = true
invocation_logs = true

[observability.traces]
enabled = true
head_sampling_rate = 1
```

Esto se replica en `[env.dev.observability]` y `[env.production.observability]`.

### Qué captura

| Tipo | Dónde se ve | Qué muestra |
|---|---|---|
| **Logs** | Cloudflare → Workers → `tm-api` / `tm-api-dev` → **Logs (beta)** | `console.log` + líneas emitidas por el middleware (`request_id`, `user`, `path`, `status`, duración) |
| **Trazas** | Mismo panel → **Traces** | Árbol de spans por invocación: entry handler → D1 queries → fetch externos |
| **Errores** | Mismo panel → **Errors** / filtro `level=error` | 4xx/5xx con stack trace, `ApiError` con el `detail` legible |
| **Mensajes personalizados en APM** | `src/lib/log.ts` → `log("info"\|"warn"\|"error", mensaje, {campos})` | p. ej. `"usuario dado de alta"` con `request_id`, `by`, `email`, `role` |

### Cómo verlo en la demo

1. Entrar a https://dash.cloudflare.com → **Workers & Pages → tm-api-dev → Logs**
2. "Begin log stream" → emitir una request desde https://tm-web-dev.pages.dev
3. Mostrar la línea en tiempo real (app, duración, status)
4. Click → se abre el detalle con **Trace** (spans) y **Logs** (los `log(...)` del middleware)
5. Para ver errores: filtro **Status: Error** → entrar a un 500 cualquiera o provocar uno temporalmente

---

## 14. Métricas (CPU, memoria, requests)

### Dashboard de Workers

En `https://dash.cloudflare.com → Workers & Pages → <worker>`:

| Métrica | Panel | Granularidad |
|---|---|---|
| **Requests** | "Overview" → gráfico "Requests" | por minuto, hora o día; segmentado por status (2xx/3xx/4xx/5xx) |
| **CPU Time** | "Overview" → "CPU Time" (p50, p95, p99) | ms por invocación |
| **Wall Time** / Duration | Mismo panel | tiempo real total por invocación |
| **Success rate** | Overview | porcentaje de 2xx + 3xx |
| **Subrequests** | Overview | llamadas salientes desde el Worker |
| **Memory** | Workers no expone RAM dedicada por invocación (modelo isolate), pero la CPU Time es su equivalente presupuestario. Para contenedores del motor ver "Docker stats" en la Mac. |

### Dashboard de Pages

`https://dash.cloudflare.com → Workers & Pages → tm-web-dev / tm-web → Analytics`:
- Requests totales
- Visitas únicas
- Bandwidth
- Latencia (p50/p95)
- Códigos de respuesta

### Dashboard de D1

`https://dash.cloudflare.com → Workers & Pages → D1 → tm-dev / tm-prod → Metrics`:
- Queries ejecutadas
- Rows read / written
- Database size

### Cómo verlo en la demo

1. `dash.cloudflare.com → Workers & Pages → tm-api → Metrics`
2. Mostrar las gráficas de Requests + CPU Time (últimos 30 min)
3. Pasar a `tm-web → Analytics` → mostrar requests y bandwidth
4. Pasar a `D1 → tm-prod → Metrics` → mostrar queries recientes

---

## 15. Guion de la demo (presentación)

Cada paso cubre un criterio de la rúbrica. Tiempo total: ~15 minutos.

| # | Paso | Criterio rúbrica | Pantalla |
|---|---|---|---|
| 1 | Presentar el proyecto en una frase y abrir el README del monorepo | Descripción del proyecto | GitHub → README |
| 2 | Mostrar el diagrama de arquitectura (sección 4-6 de este doc) | Descripción de la tecnología | Este archivo |
| 3 | Mostrar la lista de ramas y PRs ejemplo, explicar `feature → develop → main` | Estrategia de ramas | GitHub → Branches / Pull requests |
| 4 | Abrir una rama nueva en local: `git checkout -b demo/change-landing-copy`; cambiar un texto del Dashboard; `git push` | Realizar un cambio en el código | Terminal + editor |
| 5 | Abrir PR a `develop` desde la UI de GitHub | — | GitHub → New PR |
| 6 | Esperar que corran los checks (lint, tsc, vitest, build, e2e-mocked, Docker) → aprobar y mergear | Autorizar el PR | GitHub → PR checks |
| 7 | En la pestaña Actions, abrir el run del push a `develop` | Ejecutar el pipeline en Ambiente Desarrollo | GitHub → Actions |
| 8 | En el job `Web (eslint + tsc + vitest)` abrir "Pruebas Web (vitest)" → ver el reporte en Checks | Ver el resultado de las pruebas unitarias | GitHub → PR Checks |
| 9 | Esperar el summary del `deploy-dev` con la URL; abrir `https://tm-web-dev.pages.dev` → login Access OTP → ver el cambio | Ver el proyecto publicado en Dev | Navegador |
| 10 | Abrir PR `develop → main`, mergear | — | GitHub → New PR |
| 11 | En Actions, abrir el run del push a `main`; esperar al job `Deploy web a prod` en estado "waiting for review" | Aprobar el pipeline a Prod | GitHub → Actions → Review deployments |
| 12 | Hacer click en "Review deployments" → **Approve and deploy** | — | GitHub → Environments gate |
| 13 | Esperar al smoke test verde; abrir la URL real de prod (del summary); ver el cambio | Ver el cambio en Prod en la URL pública | Navegador |
| 14 | Abrir `dash.cloudflare.com → Workers → tm-api → Logs` → emitir una request desde el navegador → mostrar línea con `request_id`, status, duración | APM — mensajes | Cloudflare dashboard |
| 15 | En el mismo panel abrir **Traces** → entrar a una invocación → mostrar el árbol de spans con la query a D1 | APM — trazas | Cloudflare dashboard |
| 16 | Filtrar por status Error, abrir una línea 4xx / 5xx con el stack | APM — errores | Cloudflare dashboard |
| 17 | `tm-api → Metrics` → gráficos de Requests + CPU Time | Métricas — CPU, requests | Cloudflare dashboard |
| 18 | `tm-web → Analytics` → requests + bandwidth, `D1 → tm-prod → Metrics` → queries | Métricas — requests (y proxy de memoria) | Cloudflare dashboard |

---

## 16. Entregables y accesos

### URLs públicas

- Monorepo: https://github.com/camar-on/trading_maxxing
- Infra: https://github.com/camar-on/trading_maxxing-infra
- App dev: https://tm-web-dev.pages.dev
- App prod: (ver summary del último deploy prod, `tm-web-<hash>.pages.dev`)
- Wiki: https://github.com/camar-on/trading_maxxing/wiki

### Qué revisar para calificar

| Criterio | Dónde | Cómo confirmar |
|---|---|---|
| Backlog | GitHub Issues + Project Board | https://github.com/camar-on/trading_maxxing/issues |
| Wiki MD | `docs/final/*.md` + wiki | Este archivo + los demás en `docs/final/` |
| Diagrama de infra | §5 de este doc | — |
| Diagrama de deployment | §6 | — |
| Terraform | Repo infra | Carpeta `envs/{dev,prod}` + `modules/*` |
| PRs backend | Repo monorepo | Pestaña **Pull requests → closed**, filtro `label:backend` o ver §7 |
| PRs frontend | Repo monorepo | Pestaña **Pull requests → closed**, filtro `label:web` o ver §7 |
| Pipeline backend | Actions | https://github.com/camar-on/trading_maxxing/actions/workflows/backend.yml |
| Pipeline frontend | Actions | https://github.com/camar-on/trading_maxxing/actions/workflows/frontend.yml |
| Pipeline IaC | Actions | https://github.com/camar-on/trading_maxxing-infra/actions/workflows/terraform.yml |
| Docker Hub | hub.docker.com | 3 imágenes `trading-maxxing-*` con tags sha/dev/prod |
| Service connection (secrets) | GitHub → Settings → Environments | `dev` y `prod` por repo |
| Recursos Cloudflare | Dashboard | Workers, Pages, D1, Zero Trust → Access |
| APM | Cloudflare → Workers → Logs/Traces/Errors | §13 |
| Métricas | Cloudflare → Workers/Pages/D1 → Metrics/Analytics | §14 |

### Accesos que puede necesitar el revisor

- **GitHub:** repos son privados; invitar al evaluador como **Reader** desde Settings → Collaborators.
- **Cloudflare:** solo si se requiere entrar al dashboard; alternativa es compartir pantalla en la demo.
- **App:** correo del evaluador debe estar en `ACCESS_ALLOWED_EMAILS` (Terraform variable) **y** en la tabla `users` de D1 (`INSERT INTO users (id, email, role) VALUES (lower(hex(randomblob(16))), 'correo@iteso.mx', 'admin')`).

---

## Apéndice — Diferencias vs rúbrica literal

La rúbrica menciona "Azure DevOps" y "Azure Portal". Decisión consciente del equipo:

- **Azure DevOps → GitHub:** el proyecto se tracker, versiona y publica en GitHub. La rúbrica permite "Azure DevOps o GitHub" en los entregables, así que es compatible.
- **Azure Portal → Cloudflare Dashboard:** Cloudflare es el proveedor de infraestructura. Equivale al portal donde se ven recursos creados, métricas y logs. La rúbrica dice "Azure Portal o CloudFlare".
- **ARM / Bicep → Terraform (HCP):** el proyecto usa Terraform como IaC. La rúbrica permite Terraform.
- **Application Insights → Workers Observability:** APM equivalente dentro de Cloudflare. Logs, traces y métricas nativos.

Todo lo demás (ramas, PRs, pipelines, gates manuales, Docker Hub, tests unitarios, gates a prod, demo end-to-end) está cubierto con exactamente el flujo que pide la rúbrica.
