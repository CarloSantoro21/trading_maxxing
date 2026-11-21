# Stack tecnológico

| Capa | Tecnología | Nota |
|---|---|---|
| Frontend | **Next.js 15** (App Router), TypeScript, Tailwind, shadcn/ui, Recharts | Export estático publicado en **Cloudflare Pages** (dev y prod). |
| Backend API | **FastAPI** + Pydantic sobre Python 3.12 | Web App de contenedor `tm-api-<env>` en Azure App Service. |
| Agentes y engine | **Python 3.12** con numpy y pandas | Web App de contenedor `tm-agents-<env>` (orquestador, engine y forecast en el mismo plan B1 para abaratar costos). |
| Pronóstico | **Kronos-small** con PyTorch (CPU) servido por FastAPI | Pronósticos del backtest precalculados en Parquet para liberar recursos en producción. |
| Motor de trading | **NautilusTrader** (LGPL-3.0) | Motor event-driven con núcleo Rust; `AgenticStrategy` convierte decisiones en órdenes bracket en Binance Testnet. |
| LLM | **Groq** (gpt-oss-20b) detrás de `LLMClient` | Solo `NewsAgent` y `ExplainerAgent` usan LLM. |
| Base de datos y autenticación | **Supabase Cloud** (Postgres, Auth, Realtime, RLS), plan gratuito | Un proyecto para dev y otro para prod. En local, Supabase CLI. |
| Mensajería y cache | **Azure Cache for Redis Basic C0** en la nube; contenedor `redis` en local | Canal de señales, decisiones y cola de jobs. |
| Contenedores | **Docker** y Docker Compose; imágenes en **Docker Hub** (multi-arch) | `trading-maxxing-{web,api,agents,forecast,engine}`. Tags: `sha-<commit>`, `develop`, `X.Y.Z`, `latest`. |
| Infraestructura como código | **Terraform** con provider `azurerm` en repo separado `trading_maxxing-infra` | Módulos por recurso (`modules/`) y ambientes por carpeta (`envs/dev` y `envs/prod`). |
| CI/CD | **GitHub Actions** con autenticación a Azure por **OIDC** y Cloudflare por **API Token** | Workflows separados `backend.yml` y `frontend.yml`; environments `dev` y `prod` (prod con *required reviewers*). |
| APM y métricas | **Application Insights** + **Log Analytics Workspace** (Azure Monitor) | Traces, errores y requests; métricas del App Service Plan (CPU y memoria) en el portal de Azure. |
| Calidad | pytest + pytest-cov (cobertura ≥ 70 %), Vitest, React Testing Library, ruff, ESLint, Prettier | Reporte JUnit publicado en la pestaña de Checks (`dorny/test-reporter`). |

## Por qué Azure App Service y no Kubernetes

Son dos Web Apps de contenedor sin requisitos de orquestación avanzada. AKS cobra la administración del plano de control y agrega complejidad operativa; App Service en plan **Linux B1** cubre lo que necesitamos con dos Web Apps en el mismo plan (~13 USD al mes cada plan) y permite ver CPU y memoria del plan directamente desde el portal, que es lo que evalúa la rúbrica.

## Por qué Cloudflare Pages para el frontend

Next.js con `output: "export"` genera un bundle estático. Cloudflare Pages permite dos ambientes (`tm-web-dev` y `tm-web`, con URLs `*.pages.dev`), builds por push y HTTPS por default, sin costo en el plan gratuito.

## Por qué Supabase se queda en la nube pública

Postgres, Auth, Realtime y RLS vienen integrados y el plan gratuito alcanza para dev y prod con proyectos separados. No se mueve a Azure Database for PostgreSQL porque la RLS y el Realtime son parte del diseño del producto.

## Resumen de flujos

- **Desarrollo:** `docker compose` levanta api, agents, forecast, engine y Redis; Supabase CLI corre local; `pnpm dev` corre el frontend.
- **Dev en la nube:** push a `develop` → GitHub Actions construye, publica a Docker Hub, despliega a App Service `tm-api-dev` y `tm-agents-dev` y a Cloudflare Pages `tm-web-dev`.
- **Prod:** push o merge a `main` → mismo pipeline, pero el job de prod espera aprobación manual (GitHub Environments) antes de desplegar.
