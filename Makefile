# Atajos para el entorno local. `make help` lista los comandos.
.DEFAULT_GOAL := help
COMPOSE := docker compose

.PHONY: help up up-mps down logs ps web forecast-mps supabase-up supabase-down user test lint fmt

ROLE ?= member

help: ## Lista los comandos
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

.env:
	cp .env.example .env

supabase-up: ## Levanta Supabase local (Postgres, Auth, Realtime)
	@if [ -d supabase ]; then supabase start; else echo "supabase/ aún no existe (issue #6); se omite"; fi

supabase-down: ## Detiene Supabase local
	@if [ -d supabase ]; then supabase stop; fi

# Sin SUPABASE_SERVICE_ROLE_KEY en el entorno, toma la URL y la llave de `supabase status`
# (stack local). Para la nube: SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... make user ...
user: ## Da de alta un usuario con contraseña temporal: make user EMAIL=ana@x.dev ROLE=member|admin
	@if [ -z "$(EMAIL)" ]; then echo "Uso: make user EMAIL=correo ROLE=member|admin"; exit 1; fi
	@if [ -z "$$SUPABASE_SERVICE_ROLE_KEY" ]; then \
	  eval "$$(supabase status -o env 2>/dev/null | grep -E '^(API_URL|SERVICE_ROLE_KEY)=')"; \
	  if [ -z "$$SERVICE_ROLE_KEY" ]; then echo "Supabase local no está corriendo (make supabase-up)"; exit 1; fi; \
	  export SUPABASE_URL="$$API_URL" SUPABASE_SERVICE_ROLE_KEY="$$SERVICE_ROLE_KEY"; \
	fi; \
	uv run --package tm-api python -m tm_api.users --email "$(EMAIL)" --role "$(ROLE)"

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
