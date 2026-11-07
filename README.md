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

Cómo contribuir: [CONTRIBUTING.md](CONTRIBUTING.md).

Proyecto académico. Opera solo en entornos de prueba y no es asesoría financiera.
