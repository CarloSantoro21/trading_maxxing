# El motor en la Mac (runner)

Issue #116. La parte "web" de trading_maxxing vive en el plan gratuito de Cloudflare (Pages,
Worker `tm-api[-dev]`, D1 `tm-dev`/`tm-prod`, Access). El **motor** (orquestador, motor de
ejecución, pronósticos y Redis) corre en la Mac de Carlo con Docker y las imágenes de Docker
Hub. No hay servidor propio ni base de datos en la Mac: todo lo persistente está en D1 detrás
de la API.

```
                 ┌──────────── Cloudflare (plan gratuito) ────────────┐
 Navegador ──▶  │ Access ─▶ Pages (Next.js) ──/api/*──▶ Worker ──▶ D1 │
                 │                         Access (service token) ▲    │
                 └──────────────────────────────────────────────── │ ──┘
                                                 /internal/* (HTTPS │ saliente)
 ┌─────────────── Mac de Carlo: docker-compose.runner.yml ───────── │ ──┐
 │  agents ──tm:decisions──▶ engine ──────▶ Binance Spot Testnet (saliente) │
 │    │  ▲        (Redis)        │                                          │
 │    ▼  │                       └──▶ fills, latido, backtests ─────────────┘
 │  forecast (Kronos)    watchtower (actualiza agents/engine/forecast)      │
 └──────────────────────────────────────────────────────────────────────────┘
```

* **Solo conexiones salientes.** La Mac no abre puertos: el motor *llama* a Binance y a la
  API. No hace falta IP fija, túnel ni redirección de puertos.
* **Un solo contrato.** Todo pasa por `/internal/*` ([`api.md`](api.md), sección 3) con el
  cliente común `tm_shared.api_client.WorkerApiClient`.

## Qué hace cada contenedor

| Servicio | Imagen | Qué hace contra la API |
|---|---|---|
| `redis` | `redis:7.4-alpine` | Streams internos del motor (`tm:signals`, `tm:decisions`, `tm:shadow`) y el cursor del ledger de referencia. Con AOF en el volumen `redis-data`. |
| `agents` | `trading-maxxing-agents` | Estrategias (`/internal/strategies`), bots en `running` (`/internal/bots`), comandos `start`/`stop`/`kill` (`GET /internal/commands?after=<seq>` + `ack`), libro de cada bot (`/internal/trades`), ledger de referencia (`/internal/reference/*`), auditoría y explicaciones (`/internal/decisions`), equity de los bots (`/internal/equity-snapshots`), interruptores (`/internal/settings`) y latido `agents`. |
| `engine` | `trading-maxxing-engine` | NautilusTrader contra Binance Spot Testnet. Fills a `/internal/trades`, guardia de sleeves (`/internal/strategies`), backtests encolados (`/internal/backtests`) y latido `engine`. |
| `forecast` | `trading-maxxing-forecast` | Kronos en CPU (Docker en Mac no tiene Metal). Solo lo llama `agents` por la red interna. |
| `watchtower` | `containrrr/watchtower:1.7.1` | Cada 5 minutos revisa Docker Hub y reinicia **solo** los contenedores con la etiqueta `com.centurylinklabs.watchtower.enable=true` (agents, engine, forecast) si hay una imagen nueva con el mismo tag. |

Las imágenes son `${DOCKERHUB_NAMESPACE}/trading-maxxing-<svc>:${TM_ENV}` con `TM_ENV=prod`
(rama `main`, API `tm-api`) o `dev` (rama `develop`, API `tm-api-dev`).

## Cómo correrlo

Requisitos: Docker Desktop (con "Start Docker Desktop when you sign in") y las salidas de
Terraform de `trading_maxxing-infra`.

1. Llena las variables:

   ```bash
   cp .env.runner.example .env.runner
   ```

   | Variable | De dónde sale |
   |---|---|
   | `DOCKERHUB_NAMESPACE`, `TM_ENV` | Cuenta de Docker Hub y `prod` o `dev`. |
   | `TM_API_URL` | `https://tm-api.<subdominio>.workers.dev` (o `tm-api-dev`), **sin** `/internal`. |
   | `TM_SERVICE_TOKEN` | El secreto `SERVICE_TOKEN` del Worker (`wrangler secret put SERVICE_TOKEN --env <env>`). Va en `X-Service-Token`. |
   | `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET` | Outputs `motor_service_token_client_id` / `motor_service_token_client_secret` de Terraform (app de Access que protege `/internal/*`). Van en `CF-Access-Client-Id` / `CF-Access-Client-Secret`. |
   | `BINANCE_TESTNET_API_KEY`, `BINANCE_TESTNET_API_SECRET` | https://testnet.binance.vision. Sin ellas el motor arranca en modo `offline`. |

2. Levanta el motor:

   ```bash
   make runner-up        # pull + up -d --wait + estado
   make runner-status    # servicio, imagen y health de cada contenedor
   make runner-logs      # logs en vivo (JSON por línea)
   make runner-pull      # trae ya la imagen nueva, sin esperar a watchtower
   make runner-down      # detiene todo (conserva los volúmenes)
   ```

3. Comprueba desde el frontend (o con `curl` y la cookie de Access) que
   `GET /api/engine/status` dice `online: true` con los componentes `agents` y `engine`.

Todos los servicios tienen `restart: unless-stopped` y *healthcheck* (archivo de latido de
menos de 60 s para agents y engine; `GET /health` para forecast; `redis-cli ping` para redis):
si un proceso se cae, Docker lo levanta otra vez, y al reiniciar Docker Desktop vuelven solos.

### Que la Mac no se duerma: `caffeinate`

macOS suspende la Mac cuando está inactiva y con ella a Docker. Mientras el motor deba operar:

```bash
caffeinate -dims          # en una terminal aparte; Ctrl+C para soltar
# o atado a la vida de Docker Desktop:
caffeinate -dims -w "$(pgrep -x 'Docker Desktop' | head -1)" &
```

`-i` evita el reposo por inactividad, `-s` el del sistema (solo con corriente), `-d` el de la
pantalla y `-m` el del disco. Con la tapa cerrada la Mac duerme de todos modos salvo con
monitor externo y corriente; en una laptop conviene dejarla abierta y conectada. Alternativa
persistente (solo con corriente): `sudo pmset -c sleep 0` (y `sudo pmset -c sleep 10` para
revertir).

## Qué pasa si se cae la Mac

La parte web **sigue funcionando**: Pages, el Worker y D1 no dependen de la Mac.

| Pieza | Con la Mac apagada | Al volver |
|---|---|---|
| Frontend y API | Funcionan; el dashboard muestra los últimos datos. | — |
| `/api/engine/status` | Pasa a `online: false` cuando el último latido tiene más de `ENGINE_STALE_SECONDS` (120 s). | Vuelve a `online` con el primer latido (≤ 30 s). |
| Comandos `start`/`stop`/`kill` | La API los acepta y los deja en la tabla `commands` (el estado del bot ya cambió en D1). | `agents` lee desde `after=0` y aplica los pendientes; cada uno queda con `ack`. Además, el `sync` con `/internal/bots?status=running` alinea los bots con la base. |
| Backtests | Se quedan en `queued`. | Se corren en orden (`BACKTEST_POLL_SECONDS`). Uno que estaba en `running` cuando se apagó se queda así: hay que volver a pedirlo. |
| Señales y decisiones | No se generan. Las velas que se perdieron no se recuperan (la capa compartida recupera a lo más 60 minutos). | Siguen con la siguiente vela. |
| Posiciones abiertas en Binance | **Quedan sin protección**: el SL/TP de cada bracket se emula en el motor (Binance Spot no acepta OCO desde el adaptador), así que nadie los dispara mientras la Mac está apagada. | El motor restaura las posiciones desde `trades` y vuelve a gestionarlas; cada bot reconstruye su libro virtual. |
| Ledger de referencia | Se detiene. | Retoma del cursor guardado en Redis (`tm:reference:last_signal_id`). |
| Equity de los bots | Sin fotos nuevas: la curva tiene un hueco. | Una foto cada `EQUITY_SNAPSHOT_SECONDS` (300 s). |

Si la API es la que no responde (Cloudflare caído, token equivocado): el cliente reintenta con
backoff (0.5, 1, 2… s, tope 8 s) los `GET`/`PUT`/`PATCH`; los `POST` solo si la conexión ni
siquiera se abrió. Ninguna falla de la API tumba al motor: el latido, la auditoría, la equity
y las explicaciones se registran como *warning* y se intenta en el siguiente ciclo; la guardia
de sleeves **falla cerrado** (sin registro no se abren posiciones, los cierres sí pasan) y los
interruptores usan los últimos valores conocidos. Un `401` sugiere revisar `TM_SERVICE_TOKEN`;
un `302`/`403` con HTML, los headers de Access.

## Presupuesto del plan gratuito

Workers da 100,000 peticiones al día. El motor en reposo usa unas 27,000:

| Llamada | Cada | Por día |
|---|---|---|
| `GET /internal/commands` | 5 s (`COMMANDS_POLL_SECONDS`) | 17,280 |
| `POST /internal/heartbeat` (agents + engine) | 30 s | 5,760 |
| `GET /internal/bots?status=running` | 60 s | 1,440 |
| `GET /internal/backtests?status=queued` | 60 s | 1,440 |
| `GET /internal/settings`, `POST /internal/equity-snapshots` | 300 s | ~600 |

Más una o dos por decisión (`/internal/decisions`, explicación) y por fill (`/internal/trades`).
Si hiciera falta, `COMMANDS_POLL_SECONDS=10` baja el total a ~18,000.

## Desarrollo local

```bash
cd apps/api && pnpm dev       # wrangler dev en http://localhost:8787, D1 local
make motor-local              # build local de agents/engine/forecast + redis
make motor-local-down
```

`docker-compose.yml` (build local) apunta a `TM_API_URL=http://host.docker.internal:8787` con
el `SERVICE_TOKEN` de `apps/api/.dev.vars` (`dev-service-token-cambiame`). Si Docker no llega a
wrangler, arráncalo escuchando en todas las interfaces: `pnpm exec wrangler dev --env dev
--local --ip 0.0.0.0`. Para probar sin la API, deja `TM_API_URL` vacía: el orquestador corre
con registro y bots en memoria.

Pruebas (sin red): `uv run pytest services/agents services/engine packages`. Usan `respx` y
`tm_shared.fake_api.FakeWorkerApi`, una API simulada en memoria con las mismas respuestas y
códigos de error que el Worker.
