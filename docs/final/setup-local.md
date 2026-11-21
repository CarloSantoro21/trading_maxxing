# Setup local

Cómo levantar trading_maxxing desde cero en una Mac o Linux limpios. Para el estado del proyecto y los pendientes operativos lee [`docs/estado-del-proyecto.md`](../estado-del-proyecto.md).

## 1. Prerrequisitos

| Herramienta | Versión | Instalación sugerida |
|---|---|---|
| Docker Desktop (o Docker Engine) | 4.x con BuildKit | https://www.docker.com/products/docker-desktop |
| Python | 3.12 (ver `.python-version`) | [`uv`](https://docs.astral.sh/uv/) lo instala con `uv python install` |
| [uv](https://docs.astral.sh/uv/) | ≥ 0.4 | `brew install uv` o `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Node | 24 LTS (ver `.nvmrc`) | `nvm install && nvm use` desde la raíz |
| [pnpm](https://pnpm.io/) | 9.x | `corepack enable && corepack prepare pnpm@latest --activate` |
| [Supabase CLI](https://supabase.com/docs/guides/local-development/cli/getting-started) | 2.117.0 (misma que CI) | `brew install supabase/tap/supabase` |
| GNU Make | cualquiera reciente | viene en macOS y Linux |

Requiere hardware con al menos 8 GB de RAM libre para correr Docker, Supabase y Kronos a la vez. En Mac con Apple Silicon conviene usar `make up-mps` para correr el servicio `forecast` nativo con aceleración Metal.

## 2. Clonar y preparar el entorno

```bash
git clone https://github.com/CarloSantoro21/trading_maxxing.git
cd trading_maxxing

uv sync --all-packages          # dependencias de Python del workspace
cd apps/web && pnpm install && cd ../..
```

## 3. Configurar `.env`

```bash
cp .env.example .env   # lo hace también `make up` la primera vez
```

- **Llaves de Supabase local.** Levanta Supabase con `supabase start` y copia los valores de `supabase status`: `SUPABASE_ANON_KEY` y `SUPABASE_SERVICE_ROLE_KEY`. Son llaves de demo iguales en cualquier máquina; nunca usar en la nube.
- **Binance Testnet.** `BINANCE_TESTNET_API_KEY` y `BINANCE_TESTNET_API_SECRET`: se generan en https://testnet.binance.vision. Sin ellas se puede correr backtest y ver el dashboard, pero el motor no opera en vivo (bloqueo del issue [#11](https://github.com/CarloSantoro21/trading_maxxing/issues/11)).
- **Groq.** `GROQ_API_KEY` habilita el `NewsAgent` (vetos por noticias). Sin llave, poner `LLM_PROVIDER=fake` y el agente no clasifica. El `ExplainerAgent` depende de Groq (issue [#37](https://github.com/CarloSantoro21/trading_maxxing/issues/37)).
- **CryptoPanic.** `CRYPTOPANIC_TOKEN` para traer noticias. Sin token el `NewsAgent` queda inactivo.

**Nunca commitear `.env`.** Está en `.gitignore`. Para referencia pública, se mantiene [`.env.example`](../../.env.example).

## 4. Base de datos (Supabase local)

```bash
supabase start      # levanta Postgres, Auth, PostgREST, Realtime, Studio y Mailpit
supabase status     # URLs y llaves locales
```

URLs locales:

| Servicio | URL |
|---|---|
| API (REST, Auth, Realtime) | http://127.0.0.1:54321 |
| Postgres | `postgresql://postgres:postgres@127.0.0.1:54322/postgres` |
| Studio | http://127.0.0.1:54323 |
| Mailpit (correos de Auth) | http://127.0.0.1:54324 |

Para reiniciar la base con migraciones + seed:

```bash
supabase db reset
supabase test db    # pruebas pgTAP
```

El seed crea un **usuario demo admin**: `demo@trading-maxxing.local` / `demo1234`. También siembra perfil, meta, bot, trades, equity, backtest y las tres estrategias del MVP. Más detalles y esquema completo en [`supabase/README.md`](../../supabase/README.md).

Para dar de alta a más personas (requiere `SUPABASE_SERVICE_ROLE_KEY`):

```bash
make user EMAIL=ana@equipo.dev ROLE=member   # o ROLE=admin
```

## 5. Levantar los servicios

### Opción A — todo en contenedores (más simple)

```bash
make up          # Supabase (CLI) + Redis + api + agents + engine + forecast (CPU)
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

### Opción C — imágenes publicadas en Docker Hub

Si prefieres no construir local y ya hay secrets configurados en el repo (ver [`README.md`](../../README.md)):

```bash
export DOCKERHUB_USERNAME=<usuario> TM_TAG=develop
docker compose -f docker-compose.yml -f docker-compose.hub.yml pull
docker compose -f docker-compose.yml -f docker-compose.hub.yml up -d
```

## 6. Verificar que todo está vivo

| Servicio | URL de salud |
|---|---|
| web | http://localhost:3000 |
| api | http://localhost:8000/health · docs: http://localhost:8000/docs |
| forecast | http://localhost:8001/health |
| Redis | `docker compose exec redis redis-cli ping` → `PONG` |
| Supabase | `supabase status` |

Login con el demo:

1. http://localhost:3000/login → `demo@trading-maxxing.local` / `demo1234`.
2. El dashboard carga perfil, bot, trades y equity del seed.
3. En la pestaña de estrategias, el demo (admin) puede promover `kronos` de `shadow` a `active`.

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
make test        # pytest con cobertura + vitest
make lint        # ruff + eslint
make logs        # docker compose logs -f --tail=100
make down        # detiene contenedores (conserva volúmenes)
```

Más atajos en [`Makefile`](../../Makefile) (o `make help`).

## 9. Problemas comunes

- **`supabase start` falla porque Docker no está arriba.** Abre Docker Desktop y reintenta. El primer arranque descarga varias imágenes (10+ min).
- **La api responde 401 con un token válido.** La CLI de Supabase ≥ 2.117 firma los tokens con **ES256** y los publica en `SUPABASE_URL/auth/v1/.well-known/jwks.json`. La API los valida con el JWKS (no hace falta `SUPABASE_JWT_SECRET`). Si tu proyecto está en HS256, define `SUPABASE_JWT_SECRET`.
- **El motor no abre posiciones.** Comprueba `tm:decisions` (`docker compose exec redis redis-cli XLEN tm:decisions`) y el estado del sleeve: solo las estrategias en `active` o `probation` abren posición.
- **El dashboard no se actualiza.** Verifica la publicación Realtime (`trades`, `equity_snapshots`, `bots`, `strategies`) desde Studio.
- **El forecast tarda demasiado.** Primera llamada incluye descarga del modelo (~110 MB a `~/.cache/huggingface`). Siguientes son ms. Usa `make up-mps` en Mac.
