# Setup local

Cómo levantar trading_maxxing desde cero en una Mac o Linux limpios. La arquitectura en la nube (Cloudflare + motor en la Mac) está en [`docs/arquitectura/cloudflare.md`](../arquitectura/cloudflare.md); en local se reproduce igual: la **API** corre con `wrangler dev` sobre una **D1 local**, y el **motor** con `docker compose`. Para el estado del proyecto y los pendientes operativos lee [`docs/estado-del-proyecto.md`](../estado-del-proyecto.md).

> **Transición.** Hasta que se fusionen #116 (motor contra la API) y #117 (frontend sin Supabase), `make up` todavía levanta Supabase local para el código que no se ha migrado. Cuando terminen, `services/api` y `supabase/` se borran y esta guía queda tal cual.

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

Requiere al menos 8 GB de RAM libre para correr Docker y Kronos a la vez. En Mac con Apple Silicon conviene `make up-mps` para correr `forecast` nativo con Metal.

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
pnpm dev                                   # http://localhost:8787
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
cp .env.example .env   # lo hace también `make up` la primera vez
```

- **Conexión con la API** (mismos nombres en agents y engine):
  - `TM_API_URL=http://host.docker.internal:8787` en local (en la nube, `https://tm-api-dev.<subdominio>.workers.dev`, sin `/internal`).
  - `TM_SERVICE_TOKEN` igual al `SERVICE_TOKEN` de `apps/api/.dev.vars`; se manda como `X-Service-Token`.
  - `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET`: vacíos en local; en la nube salen de los outputs `motor_service_token_client_id/secret` del Terraform y se mandan como `CF-Access-Client-Id` / `CF-Access-Client-Secret`.
- **Binance Testnet.** `BINANCE_TESTNET_API_KEY` y `BINANCE_TESTNET_API_SECRET`, de https://testnet.binance.vision. Sin ellas se puede correr backtest y ver el dashboard, pero el motor no opera en vivo.
- **LLM y noticias: apagados por defecto** (`NEWS_ENABLED=false`, `EXPLAINER_ENABLED=false`, `LLM_PROVIDER=none`). Para probarlos: `NEWS_SOURCE=rss` (gratis) o `CRYPTOPANIC_TOKEN`, y `LLM_PROVIDER` en `workers-ai` (`CF_ACCOUNT_ID`, `CF_AI_API_TOKEN`), `groq` (`GROQ_API_KEY`) o un endpoint compatible con OpenAI. Ver [`.env.example`](../../.env.example).

**Nunca commitear `.env` ni `.dev.vars`.** Están en `.gitignore`.

## 5. Levantar el motor

### Opción A — todo en contenedores (más simple)

```bash
make up          # Redis + agents + engine + forecast (CPU)
make ps          # estado y health
make web         # frontend con hot reload en http://localhost:3000
```

### Opción B — forecast nativo con MPS (Mac Apple Silicon)

```bash
make up-mps      # forecast se conecta a host.docker.internal:8001
# en otra terminal:
make forecast-mps
make web
```

Forecast con MPS reduce la latencia de Kronos aproximadamente a la mitad vs. CPU (≈ 0.6 s vs. 1.1 s por trayectoria en una Mac M5).

### Opción C — imágenes publicadas en Docker Hub (como en producción)

Es lo que corre en la Mac contra la API de la nube; Watchtower actualiza los contenedores cuando el CI publica una imagen nueva del canal.

```bash
export DOCKERHUB_USERNAME=<usuario> TM_TAG=develop   # o el canal de prod
docker compose -f docker-compose.yml -f docker-compose.hub.yml pull
docker compose -f docker-compose.yml -f docker-compose.hub.yml up -d
```

## 6. Verificar que todo está vivo

| Servicio | URL de salud |
|---|---|
| web | http://localhost:3000 |
| API (Worker local) | http://localhost:8787/api/health |
| forecast | http://localhost:8001/health |
| Redis | `docker compose exec redis redis-cli ping` → `PONG` |
| Motor conectado | `curl localhost:8787/api/engine/status` → `online: true` (latido cada 30 s) |

En el navegador:

1. http://localhost:3000: en local no hay pantalla de Access; la API entra como el usuario demo.
2. El dashboard carga perfil, bot, trades y equity del seed.
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
make test                                  # pytest con cobertura + vitest de la web
(cd apps/api && pnpm typecheck && pnpm test:coverage)   # API
make lint                                  # ruff + eslint
make logs                                  # docker compose logs -f --tail=100
make down                                  # detiene contenedores (conserva volúmenes)
```

Más atajos en [`Makefile`](../../Makefile) (o `make help`).

## 9. Problemas comunes

- **El motor responde 401 o 403 de la API.** Revisa que `TM_SERVICE_TOKEN` sea igual al `SERVICE_TOKEN` del Worker. En la nube, si la respuesta es una página HTML de Access, faltan o están mal `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET`.
- **El motor no llega a la API local.** Desde los contenedores, `localhost` es el contenedor: usa `http://host.docker.internal:8787`.
- **La web dice "motor desconectado".** No llegan latidos: revisa `make logs` del `agents` y del `engine`.
- **El motor no abre posiciones.** Comprueba `tm:decisions` (`docker compose exec redis redis-cli XLEN tm:decisions`) y el estado del sleeve: solo las estrategias en `active` o `probation` abren posición.
- **Binance responde 451 o 403.** Binance bloquea IPs de EE. UU.; corre el motor desde México o con un egreso fuera de EE. UU.
- **El forecast tarda demasiado.** La primera llamada descarga el modelo (~110 MB a `~/.cache/huggingface`). Usa `make up-mps` en Mac.
