# web

Frontend de trading_maxxing (Next.js 15, App Router, TypeScript, Tailwind, SWR). Es un
export estático que se publica en **Cloudflare Pages** (`tm-web-dev` con la rama `develop`,
`tm-web` con `main`). Todos los datos pasan por la API (`apps/api`, Cloudflare Worker + D1);
el contrato está en [`docs/arquitectura/api.md`](../../docs/arquitectura/api.md). No hay
Supabase ni llaves en el navegador.

```bash
pnpm install
pnpm dev        # http://localhost:3000; /api/* se reenvía a wrangler dev (localhost:8787)
pnpm test       # Vitest + React Testing Library (incluye la Pages Function)
pnpm lint
pnpm typecheck  # tsc de la app y de functions/ (tipos de @cloudflare/workers-types)
pnpm build          # export estático en out/
pnpm check:export   # verifica que out/ tenga todas las rutas de src/app

# E2E con Playwright (#28, #106)
pnpm exec playwright install chromium   # solo la primera vez
pnpm e2e:mocked     # flujo completo con /api/* mockeado (sin API ni Access)
APP_URL=https://tm-web-dev.pages.dev CF_ACCESS_CLIENT_ID=... CF_ACCESS_CLIENT_SECRET=... \
  pnpm e2e:uat      # UAT contra el deploy de dev
make e2e-local      # (desde la raíz) contra el stack local real: API + motor + web
```

Requiere Node 24 LTS (ver `.nvmrc` en la raíz).

## Cómo llega el navegador a la API

```
navegador ──► Cloudflare Access ──► Pages (tm-web[-dev])
                (login OTP)           ├─ out/               archivos estáticos
                                      └─ functions/api/[[path]].ts
                                             │ service binding API (env.API)
                                             ▼
                                        Worker tm-api[-dev] ──► D1 tm-dev / tm-prod
```

- **Un solo origen.** El frontend siempre llama a `/api/...` relativo
  (`src/lib/api/client.ts`). En Pages, la Pages Function `functions/api/[[path]].ts` reenvía
  la petición completa (método, URL, headers incluidos `Cf-Access-Jwt-Assertion` y la cookie
  `CF_Authorization`, y body) al Worker por el service binding `API` y devuelve su respuesta
  sin tocarla. Sin CORS y sin exponer la URL del Worker. Si falta el binding responde 503;
  si el Worker no contesta, 502.
- **Solo `/api/*` invoca la función.** `wrangler pages deploy out` (desde `apps/web`) toma
  `functions/` y genera el `_routes.json`; el resto del sitio se sirve estático. Las pruebas de
  la función están en `tests/functions/` (fuera de `functions/` para que Pages no las tome
  como rutas) y compilan con `functions/tsconfig.json`.
- **`next build` no choca con `functions/`:** Next.js solo mira `src/app`, y el
  `tsconfig.json` de la app excluye `functions/` y `tests/functions/` (tienen sus propios tipos
  de Workers).

## Local

| Qué | Cómo |
| --- | --- |
| API | `cd apps/api && pnpm dev` (`wrangler dev --env dev --local`, puerto 8787, `AUTH_MODE=dev`) |
| Frontend | `pnpm dev` en `apps/web` |

En desarrollo `next.config.ts` quita `output: "export"` y agrega un rewrite de `/api/:path*`
a `API_DEV_URL` (por defecto `http://localhost:8787`). Un export estático no admite rewrites,
por eso el rewrite solo existe con `NODE_ENV=development`; en el build de producción la
función de Pages hace ese trabajo. Sin Access, la API con `AUTH_MODE=dev` responde
`GET /api/me` con el usuario demo (`demo@trading-maxxing.local`).

## Variables de entorno

Todas son opcionales. Las `NEXT_PUBLIC_*` son públicas: Next.js las incrusta en el bundle
al construir, así que cambiar de ambiente implica reconstruir.

| Variable | Para qué | Local | Pages |
| --- | --- | --- | --- |
| `NEXT_PUBLIC_ENV` | Badge del encabezado: `local`, `dev` o `prod` | `local` (default) | `dev` en `tm-web-dev`, `prod` en `tm-web` |
| `NEXT_PUBLIC_COMMIT_SHA` | Commit del build; el badge muestra 7 caracteres | vacío ("sin versión") | `${{ github.sha }}` |
| `NEXT_PUBLIC_API_BASE` | Origen de la API si no es el mismo del sitio | vacío | vacío |
| `API_DEV_URL` | Solo `pnpm dev`: a dónde reenvía `/api/*` | `http://localhost:8787` | – |

El badge de ambiente y versión sirve para enseñar en la demo qué commit está desplegado en
cada ambiente.

## Autenticación

- **Cloudflare Access** (Zero Trust, One-time PIN por correo) protege el dominio de Pages
  antes de que cargue el sitio. No hay formulario de login en la app ni `/login`.
- `RequireAuth` (`src/components/auth/RequireAuth.tsx`) pide `GET /api/me`
  (`{id, email, role, profile}`) y lo expone con `useMe()`. Si la API responde **401/403**
  (el correo pasó Access pero no está dado de alta en la tabla de usuarios), muestra
  "No tienes acceso" con la indicación de pedir al admin que lo dé de alta y una liga para
  entrar con otro correo.
- **Cerrar sesión** es navegar a `/cdn-cgi/access/logout` (borra la cookie de Access).
- Si la sesión de Access vence a media navegación, Access responde con una redirección; el
  cliente no la sigue (`redirect: "manual"`) y la trata como 401.
- `/` decide el destino: `/onboarding` si `me.profile` es `null`, si no `/dashboard`.

## Páginas

| Ruta | Lee / escribe |
| --- | --- |
| `/dashboard` | `GET /api/dashboard` (polling cada 5 s), `POST /api/bots`, `POST /api/bots/{id}/start\|stop\|kill` |
| `/strategies` | `GET /api/strategies/overview` (polling cada 5 s); admins: `POST /api/strategies/{name}/promote\|retire` |
| `/backtests` | `GET /api/lab/reports`, `GET /api/lab/reports/{name}`, `GET/POST /api/backtests` (lista cada 15 s) |
| `/goals` | `GET /api/goals`, `GET /api/goals/{id}/probability` |
| `/onboarding` | `GET /api/onboarding`, `PUT /api/onboarding` (perfil y metas en un batch) |
| `/admin` (solo admins) | `GET/POST /api/admin/users`, `PATCH/DELETE /api/admin/users/{id}`, `GET/PUT /api/admin/settings`, `POST /api/admin/debug/error` |

- **Encabezado** (`AppShell`): navegación (la liga *Admin* solo para admins), indicador
  "Motor en línea / desconectado" con `GET /api/engine/status` (cada 15 s; desconectado si
  el último latido del motor de la Mac es más viejo que `stale_after_seconds` o si la API
  falla), badge de ambiente y versión, correo y *Cerrar sesión*.
- **Tiempo real = polling con SWR** (`src/components/providers/ApiProvider.tsx`). La llave
  de cada consulta es la ruta de la API. SWR no refresca con la pestaña oculta
  (`refreshWhenHidden: false`) ni sin red, y vuelve a pedir los datos al regresar a la
  pestaña. Las respuestas de los botones del bot se aplican al momento sin esperar el
  siguiente refresco.
- **Admin**: alta (correo y rol), cambio de rol y baja con confirmación de usuarios (no te
  deja quitarte el admin ni darte de baja a ti mismo), interruptores `news_enabled` y
  `explainer_enabled` que el motor lee en cada ciclo (`GET /internal/settings`), y el botón
  de la demo de APM que provoca un 500 controlado y muestra el `request_id` para buscarlo en
  Workers Observability. Dar de alta en la app no basta: el correo también debe estar en la
  allowlist de Access (Terraform de `trading_maxxing-infra`).
- **Onboarding**: wizard de tres pasos (#78): capital (mínimo 500 USDT), nivel de riesgo del
  1 al 5 con la caída máxima en dólares, y metas. La API deriva `max_drawdown` del nivel.
  Validación con zod en `src/lib/onboarding/schema.ts`, alineada con la API; `RISK_TABLE` es
  copia de `tm_agents.risk.RISK_TABLE` (`services/agents/tests/test_risk_levels_sync.py`
  falla si se desalinean).

## Export estático

`pnpm build` genera HTML, JS y CSS en `out/` (`index.html`, `dashboard.html`, …,
`404.html` y `_next/static/`). Por eso:

- No hay render en servidor, route handlers (`route.ts`), middleware, server actions ni
  `next start`. Todo lo dinámico se hace en el navegador contra `/api`.
- `next/image` va con `images.unoptimized`.
- Las rutas dinámicas (`[id]`) requerirían `generateStaticParams`; hoy no hay ninguna (el
  detalle de un reporte usa `?report=` en `/backtests`).
- `scripts/check-export.mjs` deriva las rutas esperadas de los `page.tsx` de `src/app`,
  comprueba que estén en `out/` y falla si aparece un route handler, middleware o segmento
  dinámico. Corre en CI (job web) y dentro del build de la imagen.

## Imagen Docker

El `Dockerfile` construye el export y lo sirve con `nginxinc/nginx-unprivileged` (uid 101,
sin root) en el puerto 3000, con `HEALTHCHECK` contra `/healthz`. nginx no reenvía `/api`,
así que la imagen llama directo al Worker local (`NEXT_PUBLIC_API_BASE`, por defecto
`http://localhost:8787`, que en el ambiente dev de la API tiene CORS para `localhost:3000`).

```bash
docker build -t trading-maxxing-web \
  --build-arg NEXT_PUBLIC_ENV=local \
  --build-arg NEXT_PUBLIC_COMMIT_SHA=$(git rev-parse --short HEAD) apps/web
docker run --rm -p 127.0.0.1:3000:3000 trading-maxxing-web
```

## Pruebas

Las pruebas unitarias no usan red:

- `src/test/api.ts` → `mockApi(routes)` reemplaza `fetch` con rutas `"MÉTODO /ruta"` (sin
  `/api`) que responden un cuerpo, `{status, body}` o una función de la llamada, y registra
  cada llamada (`calls`, `actions()`) para verificar qué se escribe. `GET /me` y
  `GET /engine/status` tienen respuestas por defecto (usuario demo, motor en línea).
- `src/test/render.tsx` → `renderWithApi(ui)` envuelve en SWR con caché nueva por prueba.
- `tests/functions/proxy.test.ts` prueba la Pages Function con un service binding falso.

### E2E con Playwright

- **Mocked** (`pnpm e2e:mocked`): `tests/e2e/full-flow.mocked.spec.ts` intercepta `/api/*`
  con `page.route()` (`tests/e2e/mocks.ts`, con estado en memoria) y recorre inicio →
  onboarding → dashboard → crear y arrancar el bot → la operación aparece sola por polling,
  además de la pantalla de "No tienes acceso". Levanta `pnpm dev` solo (puerto `E2E_PORT`,
  3000 por defecto) y precompila las páginas antes de navegar.
- **UAT contra dev** (`pnpm e2e:uat`): `tests/e2e/uat.spec.ts` apunta a `APP_URL` y manda
  el service token de Access (`CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET`) en cada
  petición. Verifica `GET /api/health` por el mismo origen (Pages Function → Worker), que
  el sitio carga y, si la identidad del token está dada de alta, el badge (`EXPECTED_ENV`),
  el onboarding o el dashboard y `/strategies`. Si no está dada de alta valida la pantalla de
  "No tienes acceso". Sin `APP_URL` en local hace `test.skip`; en CI falla.
- **Stack local real** (`make e2e-local` desde la raíz): `tests/e2e/local-stack.spec.ts`, sin
  mocks, contra `make dev` (wrangler dev + D1 local + motor en Docker con la estrategia de
  demo). Arranca el bot y espera hasta 6 minutos a que el motor registre una operación y el
  dashboard la muestre. Sin `LOCAL_STACK=1` se omite. Guía: [`docs/correr-local.md`](../../docs/correr-local.md).

Los artefactos (`playwright-report/`, `test-results/`) están en `.gitignore`.

## Despliegue en Cloudflare Pages

Los proyectos de Pages, el service binding `API`, la app de Access y sus políticas se crean
con el Terraform de `CarloSantoro21/trading_maxxing-infra` (outputs `pages_project_name`,
`access_team_domain`, `access_web_aud`, …). El deploy desde CI es:

```bash
cd apps/web
NEXT_PUBLIC_ENV=dev NEXT_PUBLIC_COMMIT_SHA=$GITHUB_SHA pnpm build
pnpm check:export
npx wrangler pages deploy out --project-name tm-web-dev --branch develop
```

`wrangler pages deploy` debe correr desde `apps/web` para que suba `functions/` junto con
`out/`.
