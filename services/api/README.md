# api

API REST (FastAPI): perfiles, metas, bots, portafolio, trades y backtests.

```bash
uv run --package tm-api uvicorn tm_api.main:app --reload --port 8000
# Documentación interactiva: http://localhost:8000/docs  (OpenAPI en /openapi.json)
```

## Endpoints

| Tag | Rutas |
| --- | --- |
| profiles | `GET/POST /profiles`, `GET /profiles/me`, `GET/PATCH/DELETE /profiles/{id}` |
| goals | `GET/POST /goals`, `GET/PATCH/DELETE /goals/{id}` |
| bots | `GET/POST /bots`, `GET/PATCH/DELETE /bots/{id}`, `POST /bots/{id}/start`, `POST /bots/{id}/stop`, `POST /bots/{id}/kill`, `POST /bots/kill-all` |
| portfolio | `GET /portfolio` (sleeves por bot y totales) |
| trades | `GET /trades?bot_id=&symbol=&state=open\|closed&limit=&offset=` |
| backtests | `GET/POST /backtests`, `GET /backtests/{id}` |
| strategies | `GET /strategies`, `GET /strategies/{name}`, `POST /strategies/{name}/promote` y `/retire` (solo admin) |
| users | `GET /me` (id, correo y rol), `POST /admin/users` (solo admin; alta con contraseña temporal) |

Las validaciones de Pydantic replican los checks de la base (riesgo 1–5, capital mínimo de
500 USDT, retorno objetivo > 0, nombre de bot de 1 a 100 caracteres), así que los errores
salen como 422 con el campo exacto. El perfil solo recibe `risk_level` y `capital` (#78):
la base deriva `max_drawdown` del nivel y pone `horizon = medium` e `instruments =
[BTCUSDT, ETHUSDT]`; mandar esos campos da 422.

## Roles (#67)

Cada usuario es `admin` o `member` (tabla `app_roles`; sin fila, `member`). Promover o
retirar estrategias y dar de alta usuarios son acciones de admin: un member recibe 403
(dependencia `require_admin`), y la base lo vuelve a exigir con RLS. `POST /admin/users`
necesita `SUPABASE_SERVICE_ROLE_KEY`; sin ella responde 503.

## Autenticación

Todas las rutas salvo `/health` piden `Authorization: Bearer <access_token>` de Supabase
Auth. Con la CLI 2.117 el stack local firma los tokens de usuario con **ES256** y publica
la llave en `SUPABASE_URL/auth/v1/.well-known/jwks.json`; la API la descarga una vez y la
guarda en caché. Si el token viene en **HS256** (proyectos con secreto compartido) se
valida con `SUPABASE_JWT_SECRET`.

## Acceso a datos y RLS

La API no usa la service role salvo para el alta de usuarios: reenvía el JWT del usuario a PostgREST
(`SUPABASE_URL/rest/v1`) junto con la llave pública en `apikey`, y PostgreSQL aplica RLS.
Una fila ajena simplemente no aparece (404). Las migraciones permiten al rol
`authenticated` todo lo que necesita: CRUD de perfiles, metas y bots, lectura de trades y
equity, y encolar backtests en estado `queued`.

## Comandos en Redis

| Stream | Entrada | Quién la consume |
| --- | --- | --- |
| `tm:commands` | `type` (`start`/`stop`/`kill`), `bot_id`, `user_id`, `reason`, `issued_at`, `payload` (JSON) | engine |
| `tm:backtests` | `type=backtest`, `backtest_id`, `profile_id`, `user_id`, `issued_at`, `config` (JSON) | worker de backtests |

`kill` es el paro de emergencia: el bot queda `killed` y el payload lleva
`close_positions=true` (en spot: vender a USDT). Si Redis no responde, `start` se revierte,
`stop`/`kill` conservan el estado en la base y la API responde 503.

## Variables de entorno

| Variable | Uso |
| --- | --- |
| `SUPABASE_URL` | URL de Supabase (en contenedor: `http://host.docker.internal:54321`) |
| `SUPABASE_ANON_KEY` o `SUPABASE_PUBLISHABLE_KEY` | Llave pública para el header `apikey` |
| `SUPABASE_SERVICE_ROLE_KEY` o `SUPABASE_SECRET_KEY` | Solo para `POST /admin/users` (opcional) |
| `SUPABASE_JWT_SECRET` | Solo para tokens HS256 (opcional) |
| `SUPABASE_JWT_ISSUER` | Si se define, se verifica el claim `iss` |
| `REDIS_URL` | Redis de los streams |

## Pruebas

```bash
uv run pytest services/api            # sin red: base en memoria con RLS, fakeredis, respx
# Integración opcional contra Supabase local (se omite en CI):
eval "$(supabase status -o env | grep -E '^(API_URL|ANON_KEY|SERVICE_ROLE_KEY)=')"
SUPABASE_URL=$API_URL SUPABASE_ANON_KEY=$ANON_KEY SUPABASE_SERVICE_ROLE_KEY=$SERVICE_ROLE_KEY \
  uv run pytest services/api -m integration
```
