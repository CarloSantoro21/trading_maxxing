# Setup local

Cómo levantar trading_maxxing desde cero en una Mac o Linux limpios. La arquitectura en la nube (Cloudflare + motor en la Mac) está en [`docs/arquitectura/cloudflare.md`](../arquitectura/cloudflare.md); en local se reproduce igual: la **API** corre con `wrangler dev` sobre una **D1 local**, y el **motor** con `docker compose`. Para el estado del proyecto y los pendientes operativos lee [`docs/estado-del-proyecto.md`](../estado-del-proyecto.md).

> **Atajo:** `make dev` hace los pasos 3 a 5 de esta guía de un jalón (API + web + motor con la estrategia de demo y ejecución simulada) y `make e2e-local` lo prueba de punta a punta. Guía paso a paso y qué ver en cada página: [`docs/correr-local.md`](../correr-local.md). Esta página explica cada pieza por separado.

## 1. Prerrequisitos

| Herramienta | Versión | Instalación sugerida |
|---|---|---|
| Docker Desktop (o Docker Engine) | 4.x con BuildKit | https://www.docker.com/products/docker-desktop |
| Python | 3.12 (ver `.python-version`) | [`uv`](https://docs.astral.sh/uv/) lo instala con `uv python install` |
| [uv](https://docs.astral.sh/uv/) | ≥ 0.4 | `brew install uv` o `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Node | 24 LTS (ver `.nvmrc`) | `nvm install && nvm use` desde la raíz |
| [pnpm](https://pnpm.io/) | 9.x | `corepack enable` |
| `wrangler` | el de `apps/api/package.json` | nada global: `pnpm exec wrangler ...` dentro de `apps/api` |
| GNU Make | cualquiera reciente | viene en macOS y Linux |

Requiere al menos 8 GB de RAM libre para correr Docker y Kronos a la vez. Kronos es opcional (`make dev FORECAST=1`); en Mac con Apple Silicon conviene `make forecast-mps` para correrlo nativo con Metal.

## 2. Clonar y preparar el entorno

```bash
git clone https://github.com/CarloSantoro21/trading_maxxing.git
cd trading_maxxing

uv sync --all-packages                     # dependencias de Python del workspace
(cd apps/api && pnpm install)              # API (Hono + Workers)
(cd apps/web && pnpm install)              # frontend
```

## 3. API y base de datos (Workers + D1 local)

```bash
cd apps/api
cp .dev.vars.example .dev.vars             # SERVICE_TOKEN para /internal/*
pnpm db:migrate:local                      # migraciones a la D1 local (.wrangler/state)
pnpm db:seed:local                         # usuario demo admin, perfil, bot, trades y estrategias
pnpm exec wrangler dev --env dev --local --ip 0.0.0.0   # http://localhost:8787 (0.0.0.0: para que Docker llegue)
```

En local la API usa `AUTH_MODE=dev`: no hay Access y el usuario es `demo@trading-maxxing.local` (o el del header `X-Dev-User`). Pruebas rápidas:

```bash
curl localhost:8787/api/health
curl localhost:8787/api/me
curl -H 'X-Service-Token: dev-service-token-cambiame' 'localhost:8787/internal/bots?status=running'
```

Detalle de comandos, ambientes y pruebas en [`apps/api/README.md`](../../apps/api/README.md); contrato en [`docs/arquitectura/api.md`](../arquitectura/api.md).

## 4. Configurar el `.env` del motor

```bash
cp .env.example .env   # opcional: sin .env todo corre con los valores por omisión
```

- **Conexión con la API** (mismos nombres en agents y engine):
  - `TM_API_URL=http://host.docker.internal:8787` en local (en la nube, `https://tm-api-dev.<subdominio>.workers.dev`, sin `/internal`).
  - `TM_SERVICE_TOKEN` igual al `SERVICE_TOKEN` de `apps/api/.dev.vars`; se manda como `X-Service-Token`.
  - `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET`: vacíos en local; en la nube salen de los outputs `motor_service_token_client_id/secret` del Terraform y se mandan como `CF-Access-Client-Id` / `CF-Access-Client-Secret`.
- **Binance Testnet.** `BINANCE_TESTNET_API_KEY` y `BINANCE_TESTNET_API_SECRET`, de https://testnet.binance.vision. Sin ellas el engine usa la **ejecución simulada** (`ENGINE_EXECUTION=auto` → `sim`): llena cada decisión al precio público de Binance con 0.1 % de comisión y la registra igual que el modo real.
- **LLM y noticias: apagados por defecto** (`NEWS_ENABLED=false`, `EXPLAINER_ENABLED=false`, `LLM_PROVIDER=none`). Para probarlos: `NEWS_SOURCE=rss` (gratis) o `CRYPTOPANIC_TOKEN`, y `LLM_PROVIDER` en `workers-ai` (`CF_ACCOUNT_ID`, `CF_AI_API_TOKEN`), `groq` (`GROQ_API_KEY`) o un endpoint compatible con OpenAI. Ver [`.env.example`](../../.env.example).

**Nunca commitear `.env` ni `.dev.vars`.** Están en `.gitignore`.

## 5. Levantar el motor

### Opción A — todo junto (recomendada)

```bash
make dev         # API + web + redis/agents/engine; con FORECAST=1 también Kronos (CPU)
make dev-status  # estado y latido
make dev-down    # apaga todo
```

### Opción B — por piezas

```bash
make motor-local # redis + agents + engine (build local) contra la API en :8787
make web         # frontend con hot reload en http://localhost:3000
make forecast-mps   # opcional: Kronos nativo con Metal; FORECAST_URL=http://host.docker.internal:8001 en .env
```

Forecast con MPS reduce la latencia de Kronos aproximadamente a la mitad vs. CPU (≈ 0.6 s vs. 1.1 s por trayectoria en una Mac M5).

### Opción C — imágenes publicadas en Docker Hub (como en producción)

Es lo que corre en la Mac contra la API de la nube; Watchtower actualiza los contenedores cuando el CI publica una imagen nueva del canal.

```bash
export DOCKERHUB_NAMESPACE=<usuario> TM_ENV=dev   # o prod
docker compose -f docker-compose.yml -f docker-compose.hub.yml pull
docker compose -f docker-compose.yml -f docker-compose.hub.yml up -d
```

Contra la API de la nube (con Watchtower) se usa `docker-compose.runner.yml` y `make runner-up`: [`docs/arquitectura/motor-en-la-mac.md`](../arquitectura/motor-en-la-mac.md).

## 6. Verificar que todo está vivo

| Servicio | URL de salud |
|---|---|
| web | http://localhost:3000 |
| API (Worker local) | http://localhost:8787/api/health |
| forecast (solo con `FORECAST=1`) | http://localhost:8001/health |
| Redis | `docker compose exec redis redis-cli ping` → `PONG` |
| Motor conectado | `curl localhost:8787/api/engine/status` → `online: true` (latido cada 30 s) |

En el navegador:

1. http://localhost:3000: en local no hay pantalla de Access; la API entra como el usuario demo.
2. El dashboard carga perfil, bot (detenido), trades y equity del seed. Con **Arrancar**, la estrategia de demo `demo_pulse` abre la primera operación en 2 a 4 minutos.
3. En la pestaña de estrategias, el demo (admin) puede promover `kronos` de `shadow` a `active`.

Para dar de alta a más personas, como admin: `POST /api/admin/users` (en la nube, además hay que agregar el correo a la allowlist de Access).

## 7. Datos históricos y pronósticos de Kronos

- **Velas históricas.** No están en git. Se bajan con:
  ```bash
  uv run --package tm-marketdata python -m tm_marketdata download \
    --symbols BTCUSDT ETHUSDT --tf 1m 15m 30m 1d --start 2020-01-01 --end 2026-10-31
  ```
  Los Parquet se guardan bajo `data/bars/` (lo que montan `agents` y `engine` como `/app/data`).

- **Pronósticos de Kronos.** Para backtests sobre el sleeve de Kronos:
  ```bash
  uv run --package tm-forecast python services/forecast/scripts/precompute.py
  ```
  Tarda alrededor de **3 h** en una Mac M5 con MPS.

## 8. Comandos útiles

```bash
make test                                  # pytest con cobertura + vitest de la API y de la web
make lint                                  # ruff + tsc + eslint
make e2e-local                             # Playwright contra el stack local real
make dev-logs                              # logs del motor en vivo
make dev-down                              # detiene todo (conserva la D1 local y Redis)
make dev-reset                             # borra la D1 local y Redis (se recarga el seed)
```

Más atajos en [`Makefile`](../../Makefile) (o `make help`).

## 9. Problemas comunes

- **El motor responde 401 o 403 de la API.** Revisa que `TM_SERVICE_TOKEN` sea igual al `SERVICE_TOKEN` del Worker. En la nube, si la respuesta es una página HTML de Access, faltan o están mal `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET`.
- **El motor no llega a la API local.** Desde los contenedores, `localhost` es el contenedor: usa `http://host.docker.internal:8787`.
- **La web dice "motor desconectado".** No llegan latidos: revisa `make dev-logs` del `agents` y del `engine`.
- **El motor no abre posiciones.** Comprueba `tm:decisions` (`docker compose exec redis redis-cli XLEN tm:decisions`) y el estado del sleeve: solo las estrategias en `active` o `probation` abren posición.
- **Binance responde 451 o 403.** Binance bloquea IPs de EE. UU.; corre el motor desde México o con un egreso fuera de EE. UU.
- **El forecast tarda demasiado.** La primera llamada descarga el modelo (~110 MB a `~/.cache/huggingface`). Usa `make forecast-mps` en Mac.
