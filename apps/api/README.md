# apps/api: API en Cloudflare Workers

API de trading_maxxing en **Cloudflare Workers** con **Hono + TypeScript**, base **D1**
(SQLite) y login con **Cloudflare Access**. Reemplaza a `services/api` (FastAPI) y a
Supabase. El contrato completo está en [`docs/arquitectura/api.md`](../../docs/arquitectura/api.md).

## Requisitos

* Node 24 (`.nvmrc` en la raíz) y pnpm 9 (`corepack enable`).
* Nada global: `wrangler` es devDependency (`pnpm exec wrangler ...`).

## Correr en local

```bash
cd apps/api
pnpm i
cp .dev.vars.example .dev.vars        # SERVICE_TOKEN para /internal/*
pnpm db:migrate:local                 # aplica migrations/ a la D1 local (.wrangler/state)
pnpm db:seed:local                    # usuario demo admin, perfil, bot, trades y estrategias
pnpm dev                              # wrangler dev --env dev --local  ->  http://localhost:8787
```

```bash
curl localhost:8787/api/health
curl localhost:8787/api/me                                   # AUTH_MODE=dev: demo@trading-maxxing.local
curl -H 'X-Dev-User: otra@persona.dev' localhost:8787/api/me # 403 si no está en users
curl -H 'X-Service-Token: dev-service-token-cambiame' localhost:8787/internal/bots?status=running
```

`wrangler dev` corre primero `pnpm build:reports` (sección `[build]` de `wrangler.toml`), que
genera `src/generated/lab-reports.json` a partir de `docs/estrategias/reportes/`.

## Pruebas

```bash
pnpm typecheck          # tsc estricto (src, test y scripts)
pnpm test               # vitest dentro del runtime de Workers con D1 local
pnpm test:coverage      # + cobertura istanbul (mínimo 70 %) en coverage/ y junit en reports/junit.xml
pnpm build              # wrangler deploy --dry-run --env production (empaqueta sin publicar)
```

Las pruebas usan `@cloudflare/vitest-plugin` (el nombre nuevo de
`@cloudflare/vitest-pool-workers`): cada archivo arranca con las migraciones aplicadas a una D1
local. Cubren auth (dev y Access con un JWT firmado de prueba), el filtro por usuario que
reemplaza a RLS, admin vs member, los endpoints internos con token y los ports numéricos
(Monte Carlo, allocator, exposición, proyección).

## Ambientes y configuración

| | `dev` | `production` |
|---|---|---|
| Worker | `trading-maxxing-api-dev` | `trading-maxxing-api` |
| D1 | `trading-maxxing-dev` | `trading-maxxing-prod` |
| `AUTH_MODE` | `dev` en local; en la nube se cambia a `access` | `access` |

Variables (`[env.*.vars]` en `wrangler.toml`): `ENVIRONMENT`, `AUTH_MODE`, `ACCESS_TEAM_DOMAIN`,
`ACCESS_AUD`, `ACCESS_SERVICE_CLIENT_IDS` (opcional), `DEV_USER_EMAIL`, `CORS_ORIGINS`,
`ENGINE_STALE_SECONDS`. Secreto: `SERVICE_TOKEN` (`wrangler secret put SERVICE_TOKEN --env <env>`).

Los `database_id` y los `ACCESS_AUD` de `wrangler.toml` son **placeholders**. Para la nube:

```bash
pnpm exec wrangler d1 create trading-maxxing-dev     # copia el database_id a [env.dev]
pnpm exec wrangler d1 create trading-maxxing-prod    # copia el database_id a [env.production]
pnpm exec wrangler d1 migrations apply DB --env dev --remote
pnpm exec wrangler d1 execute DB --env production --remote \
  --command "INSERT INTO users (id, email, role) VALUES (lower(hex(randomblob(16))), 'tu@correo', 'admin')"
pnpm deploy:dev
```

(El id del primer admin puede ser cualquier texto único; los que crea la API son UUID.)

## Estructura

```
src/index.ts          entrada del Worker (solo export default)
src/app.ts            app Hono: logs, CORS, auth, rutas y manejo de errores
src/auth/             JWT de Access, modo dev y token de servicio
src/routes/           profiles, goals, bots, portfolio (+trades), backtests, strategies,
                      users (+admin), app-data (dashboard, onboarding, lab, engine), internal
src/lib/              D1, validación (zod), errores, logs, Monte Carlo, allocator, exposición,
                      proyección de metas, reportes del laboratorio
migrations/           esquema D1 (port de supabase/migrations)
seed/seed.sql         datos de desarrollo local
scripts/              build de los reportes del laboratorio
test/                 pruebas (vitest + Workers)
```
