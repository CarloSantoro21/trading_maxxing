# Stack tecnológico

| Capa | Tecnología | Nota |
|---|---|---|
| Frontend | **Next.js 15** (App Router), TypeScript, Tailwind, shadcn/ui, Recharts | Export estático en **Cloudflare Pages** (`tm-web-dev` y `tm-web`). Datos por `/api/*` con polling. |
| API | **Cloudflare Workers** con **Hono + TypeScript** (`apps/api`) | Workers `tm-api-dev` y `tm-api`. Pages le reenvía `/api/*` por service binding. |
| Base de datos | **Cloudflare D1** (SQLite) | `tm-dev` y `tm-prod`. El aislamiento por usuario lo hace la API (filtros por `user_id`). |
| Login | **Cloudflare Access** (Zero Trust) | Allowlist de correos y One-time PIN; roles `admin` y `member` en D1. |
| Agentes y orquestador | **Python 3.12** con numpy y pandas | Contenedor `agents` en la Mac de Carlo. |
| Pronóstico | **Kronos-small** con PyTorch | Contenedor `forecast` (o nativo con Metal/MPS). |
| Motor de trading | **NautilusTrader** (LGPL-3.0) | Contenedor `engine`; `AgenticStrategy` manda órdenes bracket a Binance Spot Testnet. |
| Mensajería | **Redis** (contenedor en la Mac) | `tm:signals`, `tm:decisions`, `tm:shadow`. |
| LLM y noticias | Opcionales y **apagados por defecto** | `NEWS_ENABLED`, `EXPLAINER_ENABLED`, `LLM_PROVIDER` (none, Workers AI, Groq u OpenAI-compatible); noticias por RSS. |
| Contenedores | **Docker**, Docker Compose, **Docker Hub** y **Watchtower** | Imágenes `trading-maxxing-{agents,engine,forecast}`; Watchtower actualiza la Mac. |
| Infraestructura como código | **Terraform** con el provider `cloudflare/cloudflare` | Repo separado `trading_maxxing-infra` con `envs/dev` y `envs/prod` (D1, Pages, Access). |
| CI/CD | **GitHub Actions** + `wrangler` | Environments `dev` y `prod` (prod con *required reviewers*). |
| APM y métricas | **Workers Observability** | Logs, trazas, errores, requests y CPU time. |
| Calidad | pytest + pytest-cov, Vitest (también dentro del runtime de Workers), React Testing Library, Playwright, ruff, ESLint | Cobertura ≥ 70 % en Python y en `apps/api`. |

## Por qué Cloudflare

- La rúbrica acepta **Azure Portal o Cloudflare**. Cloudflare cubre frontend, API, base,
  login, APM y métricas en el **plan gratuito**, sin tarjeta ni créditos que se acaben.
- Un solo origen (Pages + service binding al Worker) simplifica el login con Access: la cookie
  llega a la API sin CORS.
- `wrangler` despliega y hace rollback en segundos; Terraform crea D1, Pages y Access.

## Por qué el motor corre en la Mac

Python en Workers corre sobre Pyodide y no carga paquetes nativos (NautilusTrader, PyTorch);
además Workers no tiene procesos permanentes y el plan gratuito da 128 MB y 10 ms de CPU por
invocación. El motor necesita websockets abiertos con Binance todo el día y más de 1 GB de
memoria, así que corre en contenedores en la Mac. Con Workers Paid (5 USD al mes) se puede
mover a **Cloudflare Containers** con las mismas imágenes. Detalle en
[`docs/arquitectura/cloudflare.md`](https://github.com/CarloSantoro21/trading_maxxing/blob/develop/docs/arquitectura/cloudflare.md).

## Resumen de flujos

- **Desarrollo:** `pnpm dev` en `apps/api` (Worker local con D1 local) y en `apps/web`;
  `docker compose` levanta agents, engine, forecast y Redis.
- **Dev en la nube:** merge a `develop` → migraciones de D1 + `wrangler deploy --env dev`,
  `wrangler pages deploy` a `tm-web-dev`, imágenes del motor a Docker Hub y UAT.
- **Prod:** merge a `main` → aprobación del environment `prod` → mismo despliegue a `tm-api`,
  `tm-prod` y `tm-web`.
