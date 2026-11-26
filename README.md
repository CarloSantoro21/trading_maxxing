<p align="center">
  <img src="docs/img/logo.svg" alt="trading_maxxing" width="420" />
</p>

# trading_maxxing

Proyecto final de Infraestructura para el Desarrollo Continuo (ITESO).

Plataforma multiestrategia de trading de criptomonedas: un comité de agentes combina los pronósticos de [Kronos](https://github.com/shiyu-coder/Kronos) con estrategias cuantitativas con evidencia publicada en cripto (trend following y momentum intradía). Cada estrategia se valida con backtest in-sample/out-of-sample y Monte Carlo, cada operación se simula antes de ejecutarse y todo corre sobre [NautilusTrader](https://github.com/nautechsystems/nautilus_trader) en Binance Testnet (paper trading).

- **Correr todo en local (make dev):** [docs/correr-local.md](docs/correr-local.md)
- **Estado del proyecto y traspaso:** [docs/estado-del-proyecto.md](docs/estado-del-proyecto.md)
- **Documentación final (arquitectura, setup, pipeline, decisiones, demo):** [docs/final/README.md](docs/final/README.md)
- Propuesta: [docs/propuesta/Propuesta-trading_maxxing.md](docs/propuesta/Propuesta-trading_maxxing.md) ([PDF](docs/propuesta/Propuesta-trading_maxxing.pdf))
- Plan de trabajo: [Project Board](https://github.com/users/CarloSantoro21/projects/2) e [Issues](https://github.com/CarloSantoro21/trading_maxxing/issues)
- **Wiki:** https://github.com/CarloSantoro21/trading_maxxing/wiki (fuente en [`docs/wiki/`](docs/wiki/))

## Arquitectura

Todo lo público vive en **Cloudflare, plan gratuito** (0 USD); el motor corre en una Mac con Docker. Detalle en [docs/arquitectura/cloudflare.md](docs/arquitectura/cloudflare.md) y diagrama en [docs/propuesta/img/infra.png](docs/propuesta/img/infra.png).

- **Frontend:** Next.js 15 (export estático) en **Cloudflare Pages** (`tm-web-dev` / `tm-web`), detrás de **Cloudflare Access** (login por correo).
- **API:** **Cloudflare Workers** con Hono + TypeScript ([`apps/api`](apps/api/README.md), Workers `tm-api-dev` / `tm-api`); Pages le reenvía `/api/*` por service binding. Contrato: [docs/arquitectura/api.md](docs/arquitectura/api.md).
- **Base:** **D1** (`tm-dev` / `tm-prod`). APM y métricas con **Workers Observability**.
- **Motor:** agents, engine (NautilusTrader), forecast (Kronos) y Redis con `docker compose` en la Mac, imágenes de **Docker Hub** actualizadas por **Watchtower**. Solo hace conexiones salientes a Binance Spot Testnet y a `/internal/*` de la API.
- **LLM:** opcional y apagado por defecto (`NEWS_ENABLED`, `EXPLAINER_ENABLED`, `LLM_PROVIDER`).
- **CI/CD e IaC:** GitHub Actions con dev y prod ([docs/final/pipeline.md](docs/final/pipeline.md)); Terraform de Cloudflare en [trading_maxxing-infra](https://github.com/CarloSantoro21/trading_maxxing-infra).

## Estructura

```
apps/web            Frontend (Next.js 15, TypeScript, Tailwind)
apps/api            API en Cloudflare Workers (Hono + TypeScript + D1)
services/agents     Estrategias, orquestador, simulación y riesgo
services/forecast   Servicio de pronóstico con Kronos (FastAPI)
services/engine     Ejecución y backtest con NautilusTrader
packages/shared     Contratos compartidos (Pydantic)
docs/               Propuesta, arquitectura, wiki y diagramas
scripts/            Utilidades (make dev en scripts/dev, board de GitHub, publicar la Wiki)
```

## Correr todo en local

**Guía paso a paso: [docs/correr-local.md](docs/correr-local.md).** Requisitos: Docker Desktop,
Node 24 con pnpm 9, Python 3.12 con [uv](https://docs.astral.sh/uv/) y `make`.

```bash
make dev          # API (wrangler dev + D1 local) + web (next dev) + motor (docker compose)
make e2e-local    # Playwright contra ese stack: arranca un bot y espera su primera operación
make dev-down     # apaga todo
```

`make dev` deja el sistema completo operando sin ninguna llave: el motor usa la estrategia de
demo `demo_pulse` (alterna long/flat cada 2 minutos en velas de 1 minuto; **solo existe en
local**) y, sin llaves de Binance Spot Testnet, la **ejecución simulada** (fills al precio
público de Binance con la comisión del laboratorio). Con llaves en `.env` opera en Testnet.

| Servicio | URL |
|---|---|
| web | http://localhost:3000 |
| API (Worker local) | http://localhost:8787/api/health |
| Redis | localhost:6379 |
| forecast (opcional, `make dev FORECAST=1`) | http://localhost:8001/health |

Pruebas y lint (lo mismo que corre el CI):

```bash
uv sync --all-packages && uv run pytest --cov && uv run ruff check . && uv run ruff format --check .
cd apps/api && pnpm install && pnpm typecheck && pnpm test
cd apps/web && pnpm install && pnpm lint && pnpm typecheck && pnpm test && pnpm e2e:mocked
```

## Imágenes en Docker Hub

El workflow [`backend.yml`](.github/workflows/backend.yml) construye las imágenes del motor
(`trading-maxxing-{agents,engine,forecast}`) en cada PR y, en cada push, las publica para
linux/amd64 y linux/arm64 con el tag `sha-<corto>` y el del canal: **`dev`** en `develop` y
**`prod`** en `main`. Esos son los tags que sigue Watchtower en
[`docker-compose.runner.yml`](docker-compose.runner.yml) (`TM_ENV=dev|prod`). Sin los secrets
`DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN` solo construye y lo avisa en el resumen del job.

```bash
gh secret set DOCKERHUB_USERNAME --repo CarloSantoro21/trading_maxxing --body "<usuario>"
gh secret set DOCKERHUB_TOKEN --repo CarloSantoro21/trading_maxxing   # token Read & Write
```

Motor en la Mac con esas imágenes: [docs/arquitectura/motor-en-la-mac.md](docs/arquitectura/motor-en-la-mac.md)
(`make runner-up`).

Cómo contribuir: [CONTRIBUTING.md](CONTRIBUTING.md).

## Documentación final

Conjunto de guías de cierre del proyecto en [`docs/final/`](docs/final/):

| Documento | Para qué sirve |
|---|---|
| [`docs/final/README.md`](docs/final/README.md) | Índice y mapa de la documentación final |
| [`docs/final/arquitectura.md`](docs/final/arquitectura.md) | Vista end-to-end, ciclo de vida, persistencia y secretos |
| [`docs/final/setup-local.md`](docs/final/setup-local.md) | Instalación desde cero en Mac/Linux (y [`docs/correr-local.md`](docs/correr-local.md) para `make dev`) |
| [`docs/final/pipeline.md`](docs/final/pipeline.md) | CI/CD, cobertura y flujo de releases |
| [`docs/final/decisiones.md`](docs/final/decisiones.md) | ADR-lite de las decisiones clave |
| [`docs/final/demo-guion.md`](docs/final/demo-guion.md) | Guion de 10 min para la demo final |

## Wiki

- **Wiki en GitHub:** https://github.com/CarloSantoro21/trading_maxxing/wiki
- **Fuente de verdad:** [`docs/wiki/`](docs/wiki/) en este repo (`Home.md`, `Arquitectura.md`, `Diagrama-de-clases.md`, `Stack-tecnologico.md`, `Infraestructura.md`, `Deployment.md`, `Estrategia-de-ramas.md`, `Project-Board.md`, `_Sidebar.md`, `_Footer.md` e imágenes en `docs/wiki/img/`).
- **Sincronización:** `scripts/publish_wiki.sh` clona el repo `.wiki.git`, copia el contenido de `docs/wiki/` y hace push. La primera vez hay que inicializar la Wiki creando una página vacía desde la interfaz de GitHub.

```bash
scripts/publish_wiki.sh                           # usa CarloSantoro21/trading_maxxing
scripts/publish_wiki.sh otro-owner/otro-repo      # para un fork
```

Proyecto académico. Opera solo en entornos de prueba y no es asesoría financiera.
