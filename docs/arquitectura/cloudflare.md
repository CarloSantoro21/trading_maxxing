# Arquitectura final: Cloudflare (plan gratuito) + motor en la Mac

Decisión del 21 de noviembre de 2026. Sustituye a la de Azure App Service (17 de noviembre) y a
la de AWS ECS (propuesta original). Todo lo que ve el usuario vive en **Cloudflare, plan
gratuito**, sin Supabase. El **motor** de trading corre en la **Mac de Carlo** con
`docker compose` y solo hace conexiones de salida.

- Contrato de la API: [`api.md`](api.md). Código: [`apps/api`](../../apps/api/README.md).
- Orquestador y streams de Redis: [`orquestador.md`](orquestador.md).
- Terraform de Cloudflare: repo [`trading_maxxing-infra`](https://github.com/CarloSantoro21/trading_maxxing-infra).
- Rúbrica y cobertura por criterio: [`docs/rubrica.md`](../rubrica.md).
- Diagramas: [infraestructura](../propuesta/img/infra.png) y [deployment](../propuesta/img/deploy.png)
  (fuentes [`infra.py`](../propuesta/infra.py) y [`deploy.py`](../propuesta/deploy.py)).

## 1. Vista general

```
Navegador ──HTTPS──▶ Cloudflare Access (allowlist + OTP por correo)
                          │ cookie CF_Authorization
                          ▼
               Pages: tm-web-dev (develop) / tm-web (main)     Next.js export estático
                          │ /api/*  →  service binding "API" (env.API), mismo origen
                          ▼
               Worker: tm-api-dev / tm-api   (Hono + TypeScript)  ──▶  D1: tm-dev / tm-prod
                          ▲  /internal/*                         └──▶  Workers Observability
                          │  X-Service-Token + service token de Access
                          │  (solo conexiones SALIENTES desde la Mac)
Mac de Carlo (docker compose, imágenes de Docker Hub + Watchtower)
   agents (orquestador, estrategias, SimulationGate, RiskAgent)
   engine (NautilusTrader, TradingNode)  ──▶  Binance Spot Testnet
   forecast (Kronos-small, PyTorch)
   redis (tm:signals, tm:decisions, tm:shadow)
```

| Pieza | Recurso de Cloudflare / lugar | dev | prod |
|---|---|---|---|
| Frontend | Pages (export estático de Next.js) | `tm-web-dev` (rama de producción `develop`) | `tm-web` (rama `main`) |
| API | Worker con Hono + TypeScript (`apps/api`) | `tm-api-dev` | `tm-api` |
| Base de datos | D1 (SQLite) | `tm-dev` | `tm-prod` |
| Login | Access (Zero Trust): app delante del dominio de Pages, allowlist de correos y One-time PIN | app `web` dev | app `web` prod |
| Acceso del motor | Access: app sobre `/internal/*` del Worker con *service token* | token dev | token prod |
| APM y métricas | Workers Observability (logs, trazas, errores, requests, CPU time) | sí | sí |
| Motor | Mac de Carlo: `docker compose` con agents, engine, forecast, redis y Watchtower | imágenes canal dev | imágenes canal prod |
| Imágenes | Docker Hub `trading-maxxing-{agents,engine,forecast}` | | |
| IaC | Terraform `cloudflare/cloudflare` en `trading_maxxing-infra` (D1, Pages, Access) | `envs/dev` | `envs/prod` |

## 2. Un solo origen: Pages → service binding → Worker

El navegador solo habla con el dominio de Pages. Pages sirve los archivos estáticos y reenvía
`/api/*` al Worker por un **service binding** llamado `API` (`env.API.fetch(request)`). Así:

- La cookie `CF_Authorization` de Access es del mismo origen y llega al Worker sin CORS ni
  cookies de terceros.
- El Worker valida el JWT de Access (`Cf-Access-Jwt-Assertion` o la cookie) contra el JWKS del
  equipo (`https://<access_team_domain>/cdn-cgi/access/certs`) y el `aud` de la app
  (`access_web_aud`), y con el correo busca al usuario en la tabla `users` de D1.
- El service binding no sale a internet: es una llamada interna entre Workers, sin costo extra
  en el plan gratuito.

Roles (`admin` / `member`) y aislamiento por usuario los aplica la API con filtros por
`user_id` (el equivalente a RLS; ver [`api.md`](api.md) §1).

## 3. El motor en la Mac

El motor no expone puertos ni usa túneles: **solo hace conexiones salientes** a Binance Spot
Testnet y a `https://tm-api[-dev].<subdominio>.workers.dev/internal/*`.

Variables estándar del motor (las mismas en agents y engine):

| Variable | Uso |
|---|---|
| `TM_API_URL` | Base del Worker, sin `/internal` (p. ej. `https://tm-api-dev.<subdominio>.workers.dev`) |
| `TM_SERVICE_TOKEN` | Se manda en el header `X-Service-Token`; el Worker lo compara con su secreto `SERVICE_TOKEN` |
| `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` | Opcionales. Si existen, se mandan como `CF-Access-Client-Id` / `CF-Access-Client-Secret` para pasar la app de Access que protege `/internal/*` (salen de los outputs `motor_service_token_client_id/secret` de Terraform) |

Qué hace con la API (detalle en [`api.md`](api.md) §3): lee bots en `running` y comandos
(`start`/`stop`/`kill`), escribe trades, fotos de equity, decisiones, transiciones de
estrategias y un *heartbeat* que la web muestra como "motor en línea / desconectado".

Operación:

- `docker compose` con las imágenes de Docker Hub (`trading-maxxing-{agents,engine,forecast}`)
  y `redis`.
- **Watchtower** revisa Docker Hub y reinicia un servicio solo cuando el CI publica una imagen
  nueva de su canal (dev o prod). El deploy del motor es, entonces, "push a Docker Hub".
- La Mac se mantiene despierta con `caffeinate` durante el paper trading.
- `forecast` puede correr nativo con Metal (MPS) para bajar la latencia de Kronos.

## 4. Por qué el motor corre en la Mac y no en Workers

| Límite de Workers (plan gratuito) | Qué choca |
|---|---|
| **Python en Workers corre sobre Pyodide** (WebAssembly): solo paquetes puros de Python o los que trae Pyodide; **no hay paquetes nativos** | NautilusTrader (núcleo en Rust/Cython) y PyTorch (Kronos) son nativos y no cargan |
| **No hay procesos permanentes**: cada invocación responde a un request, un cron o un mensaje y termina | El `TradingNode` de Nautilus mantiene websockets con Binance y estado en memoria todo el día; el orquestador consume streams de Redis continuamente |
| **128 MB de memoria** por isolate | Kronos-small + PyTorch ocupan cientos de MB; el motor completo, más de 1 GB |
| **10 ms de CPU** por invocación | Una trayectoria de Kronos tarda ~0.6 s en MPS; el Monte Carlo de la SimulationGate, decenas de ms |

Por eso la parte "web" (API CRUD, login, base) va en Workers y D1, que encajan perfecto con el
plan gratuito, y la parte "cómputo de larga duración" va en contenedores en la Mac. El costo
total en la nube es **0 USD**.

## 5. Cuando paguemos: Cloudflare Containers

Con **Workers Paid (5 USD al mes)** se habilita **Cloudflare Containers**, que corre imágenes
OCI normales junto a los Workers. El plan de migración sin cambiar código:

- **Mismas imágenes** de Docker Hub (`agents`, `engine`, `forecast`): se publican también en el
  registro de Cloudflare desde el mismo pipeline.
- Un **Durable Object supervisor** por ambiente arranca y mantiene vivos los contenedores
  (Containers se controlan desde un Durable Object), reinicia si caen y expone su estado al
  Worker de la API.
- **Redis** como un contenedor más, o se sustituye por **Cloudflare Queues** para
  `tm:signals` / `tm:decisions` (el orquestador ya abstrae el sink y el source de streams).
- El motor deja de usar la URL pública y el service token: la API llega a los contenedores y
  ellos a la API por **service binding interno**.
- **Métricas de CPU y memoria por contenedor** en el panel de Cloudflare: cubre de lleno el
  criterio de métricas de la rúbrica, que en el plan gratuito solo podemos mostrar con CPU time
  y requests del Worker (y `docker stats` de la Mac).
- **Ojo con la región:** Binance bloquea IPs de EE. UU. (y responde 451 o 403). Hay que fijar o
  verificar que los contenedores arranquen fuera de EE. UU. (pista de ubicación o región
  permitida) o mandar las órdenes por un egreso fuera de EE. UU.; si no, el `TradingNode` no se
  conecta. Probarlo con el smoke test del engine antes de mover el paper trading.

## 6. LLM y noticias: opcionales y apagados por defecto

Desde #118 (PR #119) el `NewsAgent` y el `ExplainerAgent` están **apagados por defecto**
(`NEWS_ENABLED=false`, `EXPLAINER_ENABLED=false`, `LLM_PROVIDER=none`). Si se prenden, las
noticias vienen de RSS gratis (o CryptoPanic) y el LLM puede ser Workers AI, Groq o un
endpoint compatible con OpenAI. Un admin los puede prender en caliente desde
`PUT /api/admin/settings` (tabla `settings` de D1), que el motor lee en `/internal/settings`.
Ninguna estrategia, ni el riesgo, ni la SimulationGate dependen del LLM.

## 7. Límites del plan gratuito que nos importan

| Servicio | Límite relevante | Cómo lo respetamos |
|---|---|---|
| Workers | 100 000 requests al día, 10 ms de CPU por invocación | Pocos usuarios; el polling del dashboard es cada varios segundos; la proyección de metas limita las celdas del Monte Carlo |
| D1 | 5 GB, cuotas diarias de filas leídas y escritas | Equity por bot cada minuto, no por tick; índices en las consultas por usuario |
| Pages | 500 builds al mes | Un build por merge a `develop` o `main` |
| Access | Hasta 50 usuarios | Somos menos de 10 |
| Observability | Retención corta de logs en el plan gratuito | Suficiente para la demo; capturas guardadas en el guion |

## 8. Secretos

| Dónde | Qué |
|---|---|
| GitHub Environments `dev` y `prod` ("service connection" de la rúbrica) | `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN` |
| Secretos del Worker (`wrangler secret put --env <env>`) | `SERVICE_TOKEN` |
| `.env` de la Mac (nunca en git) | `TM_API_URL`, `TM_SERVICE_TOKEN`, `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET`, llaves de Binance Testnet y, si se usan, las del LLM o de noticias |
| Terraform (`trading_maxxing-infra`) | Variables `sensitive`; outputs `d1_database_id`, `pages_project_name`, `access_team_domain`, `access_web_aud`, `motor_service_token_client_id/secret` |
