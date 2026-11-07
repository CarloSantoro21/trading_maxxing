# trading_maxxing

Proyecto final de Infraestructura para el Desarrollo Continuo (ITESO).

Plataforma multiestrategia de trading de criptomonedas: un comité de agentes combina los pronósticos de [Kronos](https://github.com/shiyu-coder/Kronos) con estrategias cuantitativas con evidencia publicada en cripto (trend following y momentum intradía). Cada estrategia se valida con backtest in-sample/out-of-sample y Monte Carlo, cada operación se simula antes de ejecutarse y todo corre sobre [NautilusTrader](https://github.com/nautechsystems/nautilus_trader) en Binance Testnet (paper trading).

- Propuesta: [docs/propuesta/Propuesta-trading_maxxing.md](docs/propuesta/Propuesta-trading_maxxing.md) ([PDF](docs/propuesta/Propuesta-trading_maxxing.pdf))
- Plan de trabajo: [Project Board](https://github.com/users/CarloSantoro21/projects/2) e [Issues](https://github.com/CarloSantoro21/trading_maxxing/issues)
- Stack: Next.js, FastAPI, Supabase, Redis, Docker y Docker Hub, GitHub Actions, Terraform, AWS ECS Fargate

## Estructura

```
apps/web            Frontend (Next.js 15, TypeScript, Tailwind)
services/api        API REST (FastAPI)
services/agents     Estrategias, orquestador, simulación y riesgo
services/forecast   Servicio de pronóstico con Kronos (FastAPI)
services/engine     Ejecución y backtest con NautilusTrader
packages/shared     Contratos compartidos (Pydantic)
docs/               Propuesta y diagramas
scripts/            Utilidades (board de GitHub)
```

## Desarrollo local

Requisitos: Python 3.12 con [uv](https://docs.astral.sh/uv/), Node 24 LTS con pnpm y Docker.

```bash
uv sync --all-packages          # dependencias de Python (workspace)
uv run pytest --cov             # pruebas de Python con cobertura
uv run ruff check . && uv run ruff format --check .

cd apps/web && pnpm install
pnpm test && pnpm lint && pnpm dev
```

## Levantar todo en local

```bash
make up          # Supabase (CLI) + Redis + api + agents + engine + forecast (CPU)
make up-mps      # igual, con forecast nativo en la Mac (Metal); luego `make forecast-mps`
make web         # frontend con hot reload
make ps          # estado y health
make down        # detener
```

| Servicio | URL |
|---|---|
| web | http://localhost:3000 |
| api | http://localhost:8000/health |
| forecast | http://localhost:8001/health |
| Redis | localhost:6379 |
| Supabase (API / Studio) | http://localhost:54321 · http://localhost:54323 |

Copia `.env.example` a `.env` (`make up` lo hace la primera vez) y llena las llaves.

Base de datos: Supabase local con su CLI (`supabase start`, `supabase db reset`, `supabase test db`). Detalles, usuario demo y esquema en [supabase/README.md](supabase/README.md).

## Imágenes en Docker Hub

El workflow `.github/workflows/docker-publish.yml` construye (linux/amd64 y linux/arm64) y publica `trading-maxxing-{web,api,agents,forecast,engine}`. En push a `develop` publica `sha-<commit>` y `develop`; en un tag `vX.Y.Z` publica `X.Y.Z` y `latest`. Sin los secrets solo construye y lo avisa en el resumen del job (no falla).

Configurar los secrets:

1. En Docker Hub: Account Settings > Personal access tokens > Generate new token (permisos Read & Write).
2. Cargarlos en el repo:

```bash
gh secret set DOCKERHUB_USERNAME --repo CarloSantoro21/trading_maxxing --body "<usuario>"
gh secret set DOCKERHUB_TOKEN --repo CarloSantoro21/trading_maxxing   # pega el token cuando lo pida
```

Usar las imágenes publicadas en vez de construirlas:

```bash
export DOCKERHUB_USERNAME=<usuario> TM_TAG=develop   # o un X.Y.Z
docker compose -f docker-compose.yml -f docker-compose.hub.yml pull
docker compose -f docker-compose.yml -f docker-compose.hub.yml up -d
```

Cómo contribuir: [CONTRIBUTING.md](CONTRIBUTING.md).

Proyecto académico. Opera solo en entornos de prueba y no es asesoría financiera.
