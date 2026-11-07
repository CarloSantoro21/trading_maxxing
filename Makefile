# Atajos para el entorno local. `make help` lista los comandos.
.DEFAULT_GOAL := help
COMPOSE := docker compose

.PHONY: help up up-mps down logs ps web forecast-mps supabase-up supabase-down test lint fmt

help: ## Lista los comandos
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

.env:
	cp .env.example .env

supabase-up: ## Levanta Supabase local (Postgres, Auth, Realtime)
	@if [ -d supabase ]; then supabase start; else echo "supabase/ aún no existe (issue #6); se omite"; fi

supabase-down: ## Detiene Supabase local
	@if [ -d supabase ]; then supabase stop; fi

up: .env supabase-up ## Todo en contenedores (forecast en CPU)
	COMPOSE_PROFILES=forecast-docker $(COMPOSE) up -d --build --wait
	@$(MAKE) --no-print-directory ps

up-mps: .env supabase-up ## Todo, con forecast nativo en la Mac (MPS); córrelo con `make forecast-mps`
	FORECAST_URL=http://host.docker.internal:8001 $(COMPOSE) up -d --build --wait
	@echo "Ahora en otra terminal: make forecast-mps"

forecast-mps: ## Forecast nativo con aceleración Metal en http://localhost:8001
	FORECAST_DEVICE=mps uv run --package tm-forecast uvicorn tm_forecast.main:app --host 127.0.0.1 --port 8001

web: ## Frontend con hot reload en http://localhost:3000
	cd apps/web && pnpm install && pnpm dev

down: ## Detiene los contenedores (conserva volúmenes)
	COMPOSE_PROFILES=forecast-docker,web $(COMPOSE) down

logs: ## Logs en vivo
	COMPOSE_PROFILES=forecast-docker,web $(COMPOSE) logs -f --tail=100

ps: ## Estado y health de los servicios
	@COMPOSE_PROFILES=forecast-docker,web $(COMPOSE) ps

test: ## Pruebas de Python y del frontend
	uv run pytest --cov
	cd apps/web && pnpm test

lint: ## Lint y formato
	uv run ruff check . && uv run ruff format --check .
	cd apps/web && pnpm lint
