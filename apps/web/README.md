# web

Frontend de trading_maxxing (Next.js 15, App Router, TypeScript, Tailwind).

```bash
pnpm install
cp .env.example .env.local   # llena la llave anon con `supabase status`
pnpm dev        # http://localhost:3000
pnpm test       # Vitest + React Testing Library
pnpm lint
pnpm typecheck
pnpm build          # export estático en out/
pnpm check:export   # verifica que out/ tenga todas las rutas de src/app
```

Requiere Node 24 LTS (ver `.nvmrc` en la raíz).

## Export estático

`next.config.ts` usa `output: "export"`: `pnpm build` genera HTML, JS y CSS en `out/`
(`index.html`, `login.html`, `onboarding.html`, `404.html` y `_next/static/`). Por eso:

- No hay render en servidor, route handlers (`route.ts`), middleware, server actions ni
  `next start`. Todo lo dinámico (sesión, lecturas y escrituras a Supabase) se hace en el
  navegador.
- `next/image` va con `images.unoptimized`, porque el optimizador necesita un servidor.
- Las rutas dinámicas (`[id]`) requerirían `generateStaticParams`; hoy no hay ninguna.
- `scripts/check-export.mjs` deriva las rutas esperadas de los `page.tsx` de `src/app`,
  comprueba que estén en `out/` y falla si aparece un route handler, middleware o segmento
  dinámico. Corre en CI (job web) y dentro del build de la imagen; sus pruebas están en
  `scripts/check-export.test.mjs`.

Para probar `out/` localmente sin Docker: `npx serve out` (o `python3 -m http.server -d out`,
aunque este último no resuelve `/login` a `login.html`).

## Imagen Docker

El `Dockerfile` construye el export y lo sirve con `nginxinc/nginx-unprivileged` (usuario
`nginx`, uid 101, sin root) en el puerto 3000, con `HEALTHCHECK` contra `/healthz`.
`nginx.conf` imita a Cloudflare Pages: `/login` sirve `login.html` y lo desconocido, `404.html`.

```bash
docker build -t trading-maxxing-web \
  --build-arg NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:54321 \
  --build-arg NEXT_PUBLIC_SUPABASE_ANON_KEY=<anon key> apps/web
docker run --rm -p 127.0.0.1:3000:3000 trading-maxxing-web
```

Las `NEXT_PUBLIC_*` se incrustan en el build: cambiar de entorno implica reconstruir.

## Variables de entorno

| Variable | Valor local |
| --- | --- |
| `NEXT_PUBLIC_SUPABASE_URL` | `http://127.0.0.1:54321` (API de `supabase start`) |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | `anon key` de `supabase status` |
| `NEXT_PUBLIC_API_URL` | Reservada para cuando el frontend consuma `services/api` (hoy no se usa). En la nube debe ser `https://` |

Son públicas: Next.js las incrusta en el bundle durante el build. Nunca pongas aquí la
llave `service_role`. Si faltan, el build pasa igual y la app muestra un aviso.

## Autenticación y onboarding

- **Sitio estático.** En la entrega el frontend se publica con `output: "export"` en
  Cloudflare Pages, así que no hay middleware ni server actions: la sesión de Supabase Auth
  se resuelve en el navegador (`@supabase/ssr` con `createBrowserClient`) dentro de
  `AuthProvider`, y `RequireAuth` redirige a `/login?next=…` si no hay sesión. La seguridad
  real la dan las políticas RLS de la base; la protección de rutas solo evita mostrar
  pantallas que no funcionarían.
- **`/login`**: correo y contraseña. No hay registro público (#67): un admin da de alta a
  cada persona con `make user EMAIL=... ROLE=member|admin`. Usuario demo del seed (admin):
  `demo@trading-maxxing.local` / `demo1234`. Tras entrar va a `next` (solo rutas internas), a
  `/onboarding` si todavía no hay perfil o a `/` si ya lo hay.
- **`/onboarding`**: wizard de tres pasos (#78): capital (mínimo 500 USDT), nivel de riesgo
  del 1 al 5 con una explicación en lenguaje simple y la caída máxima en dólares para el
  capital capturado, y metas. Ya no pide horizonte, drawdown ni instrumentos: siempre se
  operan BTC/USDT y ETH/USDT, y la base deriva el drawdown del nivel. Escribe en `profiles`
  (upsert por `user_id`) y reemplaza las filas de `goals` con la sesión del usuario, por lo
  que RLS limita todo a sus propias filas. Si ya hay perfil, el wizard se prellena.
- **Validación** con zod en `src/lib/onboarding/schema.ts`, alineada con los checks de
  `supabase/migrations/`: capital >= 500, riesgo entero de 1 a 5, retorno objetivo > 0 % y
  fecha límite futura. `RISK_TABLE` ahí es copia de `tm_agents.risk.RISK_TABLE`;
  `services/agents/tests/test_risk_levels_sync.py` falla si se desalinean.

## Pruebas

Las pruebas no usan red: `src/test/fakeSupabase.ts` simula el cliente de Supabase (query
builder encadenable y `auth`) y registra cada llamada para verificar qué se escribe.

## Cloudflare Pages (entrega final, #45)

> **Diferido.** Todavía no hay cuenta de Cloudflare y no se ha desplegado nada. Esto
> describe lo que hay que hacer para la entrega; el código y el workflow ya están listos.

El workflow `.github/workflows/deploy-web.yml` es manual (`workflow_dispatch`), usa el
environment `production`, construye `out/`, corre `check:export` y ejecuta
`wrangler pages deploy out --project-name trading-maxxing`. Si faltan
`CLOUDFLARE_API_TOKEN` o `CLOUDFLARE_ACCOUNT_ID`, avisa en el resumen del run y termina en
verde sin desplegar.

### 1. Cuenta gratuita

1. Regístrate en <https://dash.cloudflare.com/sign-up> con correo y contraseña (plan Free;
   no pide tarjeta ni dominio propio).
2. Verifica el correo.

### 2. Proyecto de Pages (subdominio `*.pages.dev`)

El proyecto debe existir antes del primer deploy desde CI. Dos opciones:

- **Dashboard:** *Workers & Pages → Create → Pages → Use direct upload*, nombre
  `trading-maxxing`. No conectes el repo de GitHub: el build lo hace nuestro workflow.
- **CLI:** `npx wrangler login` y luego
  `npx wrangler pages project create trading-maxxing --production-branch main`.

El sitio queda en `https://trading-maxxing.pages.dev` (si el nombre está ocupado, Cloudflare
agrega un sufijo; usa el que muestre el dashboard). No se configura dominio propio. Los
deploys con otra rama (input `branch` del workflow) quedan como previews en
`https://<rama>.trading-maxxing.pages.dev`.

### 3. Token y Account ID

1. *My Profile → API Tokens → Create Token → Create Custom Token*.
2. Permiso: **Account → Cloudflare Pages → Edit**, limitado a tu cuenta. Nada más.
3. Copia el token (solo se muestra una vez).
4. El **Account ID** aparece en *Workers & Pages* (columna derecha) o en la URL del
   dashboard (`dash.cloudflare.com/<account id>/…`).

### 4. Secrets y variables en GitHub

En *Settings → Environments → New environment* crea `production` (opcional: exige
aprobación de un revisor). Dentro del environment:

| Tipo | Nombre | Valor |
| --- | --- | --- |
| Secret | `CLOUDFLARE_API_TOKEN` | token del paso 3 |
| Secret | `CLOUDFLARE_ACCOUNT_ID` | Account ID del paso 3 |
| Variable | `NEXT_PUBLIC_SUPABASE_URL` | URL del proyecto de Supabase en la nube |
| Variable | `NEXT_PUBLIC_SUPABASE_ANON_KEY` | llave `anon` de ese proyecto (pública) |
| Variable | `NEXT_PUBLIC_API_URL` | URL **HTTPS** de la API (cuando el frontend la use) |

Las `NEXT_PUBLIC_*` son públicas (terminan en el bundle), por eso van como *variables* y no
como secrets; otro entorno (p. ej. `staging`) tendría su propio juego. Nunca pongas la llave
`service_role`.

Después: *Actions → Deploy web (Cloudflare Pages) → Run workflow*. En Supabase
(*Authentication → URL Configuration*) agrega la URL de `pages.dev` como *Site URL* y en
*Redirect URLs*.

### 5. Pendiente: API por HTTPS (contenido mixto)

Pages sirve el sitio por HTTPS. Si el navegador llama a la API por `http://` (por ejemplo,
directo al ALB de AWS), lo bloquea como **contenido mixto**. Antes de que el frontend
consuma `services/api` en la nube, la API debe exponerse por HTTPS con alguna de estas
opciones:

- **CloudFront delante del ALB**: certificado gratuito en `*.cloudfront.net`, sin dominio
  propio; el ALB puede quedarse en HTTP detrás de CloudFront.
- **Cloudflare Tunnel** (`cloudflared` junto a la API): expone la API por HTTPS sin abrir
  puertos (los túneles con nombre requieren una zona en Cloudflare; los *quick tunnels*
  `*.trycloudflare.com` sirven solo para demos).

Esto queda **pendiente para la entrega**, junto con el CORS de la API para el origen de
`pages.dev`. Supabase en la nube ya se sirve por HTTPS, así que no tiene este problema.
