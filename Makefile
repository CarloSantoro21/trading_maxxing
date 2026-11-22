# Atajos para el entorno local. `make help` lista los comandos.
# Guía paso a paso: docs/correr-local.md
.DEFAULT_GOAL := help
COMPOSE := docker compose

.PHONY: help dev dev-down dev-status dev-logs dev-reset e2e-local api web motor-local \
	motor-local-down forecast-mps test lint fmt

help: ## Lista los comandos
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --- Stack local completo (docs/correr-local.md) --------------------------------------------
# API (wrangler dev + D1 local con seed) + web (next dev) + motor (docker compose) con la
# estrategia demo_pulse y ejecución simulada si no hay llaves de Binance Testnet.
#   make dev FORECAST=1      también construye y levanta Kronos (perfil forecast, pesado)
#   make dev MOTOR=native    motor con uv y redis-server en la Mac, sin Docker
dev: ## Levanta API + web + motor local con la demo y ejecución sim; imprime las URLs
	@FORECAST=$(FORECAST) MOTOR=$(MOTOR) scripts/dev/up.sh

dev-down: ## Apaga todo lo que levantó `make dev`
	@scripts/dev/down.sh

dev-status: ## Estado del stack local (API, web, motor y latido)
	@scripts/dev/status.sh

dev-logs: ## Logs del motor en vivo (API y web en .dev/logs/)
	$(COMPOSE) logs -f --tail=100 agents engine

dev-reset: ## Borra la D1 local y Redis: el siguiente `make dev` vuelve a cargar el seed
	@scripts/dev/down.sh
	rm -rf apps/api/.wrangler/state
	-$(COMPOSE) down -v
	@echo "D1 local y Redis borrados."

e2e-local: ## Playwright contra el stack local real (lo levanta si hace falta): bot -> trade -> dashboard
	@scripts/dev/e2e.sh

# --- Piezas sueltas ---------------------------------------------------------------------------
api: ## Solo la API: wrangler dev en http://localhost:8787 (D1 local)
	cd apps/api && pnpm install && pnpm db:migrate:local && pnpm exec wrangler dev --env dev --local --ip 0.0.0.0 --port 8787

web: ## Solo el frontend con hot reload en http://localhost:3000 (contra la API local)
	cd apps/web && pnpm install && NEXT_PUBLIC_ENV=local pnpm dev

motor-local: ## Solo el motor (build local) contra la API en http://localhost:8787
	TM_DEMO_STRATEGY=$${TM_DEMO_STRATEGY:-1} $(COMPOSE) up -d --build --wait redis agents engine
	@$(COMPOSE) ps

motor-local-down: ## Detiene el motor local (conserva volúmenes)
	COMPOSE_PROFILES=forecast $(COMPOSE) down

forecast-mps: ## Forecast nativo con aceleración Metal en http://localhost:8001
	FORECAST_DEVICE=mps uv run --package tm-forecast uvicorn tm_forecast.main:app --host 127.0.0.1 --port 8001

test: ## Pruebas de Python, de la API y del frontend
	uv run pytest --cov
	cd apps/api && pnpm test
	cd apps/web && pnpm test

lint: ## Lint, formato y tipos
	uv run ruff check . && uv run ruff format --check .
	cd apps/api && pnpm typecheck
	cd apps/web && pnpm lint && pnpm typecheck

fmt: ## Formatea el Python
	uv run ruff check --fix . && uv run ruff format .

# --- Motor en la Mac (docs/arquitectura/motor-en-la-mac.md) ---------------------------------
# runner-*: imágenes de Docker Hub + watchtower, con las variables de .env.runner.
RUNNER := $(COMPOSE) -f docker-compose.runner.yml --env-file .env.runner

.PHONY: runner-up runner-down runner-logs runner-status runner-pull

.env.runner:
	@echo "Falta .env.runner: cp .env.runner.example .env.runner y llena TM_API_URL, TM_SERVICE_TOKEN, ..."; exit 1

runner-up: .env.runner ## Motor en la Mac con imágenes de Docker Hub (redis, agents, engine, forecast, watchtower)
	$(RUNNER) pull --quiet
	$(RUNNER) up -d --wait
	@$(MAKE) --no-print-directory runner-status

runner-down: .env.runner ## Detiene el motor de la Mac (conserva volúmenes)
	$(RUNNER) down

runner-logs: .env.runner ## Logs en vivo del motor de la Mac
	$(RUNNER) logs -f --tail=100

runner-status: .env.runner ## Estado y health de los contenedores del motor
	@$(RUNNER) ps --format 'table {{.Service}}\t{{.Image}}\t{{.Status}}'

runner-pull: .env.runner ## Descarga ya las imágenes nuevas (sin esperar a watchtower)
	$(RUNNER) pull && $(RUNNER) up -d --wait
