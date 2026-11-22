# CI/CD

Estado de los pipelines. Las fuentes son los workflows en [`.github/workflows/`](../../.github/workflows), la guía de contribución en [`CONTRIBUTING.md`](../../CONTRIBUTING.md) y la [rúbrica oficial del curso](../rubrica.md).

> **Arquitectura final (21-nov-2026).** Todo lo público va a **Cloudflare, plan gratuito**: el backend es el Worker `tm-api-dev` / `tm-api` (Hono + D1) y el frontend, Pages `tm-web-dev` / `tm-web`. El motor (agents, engine, forecast) se publica a **Docker Hub** y lo actualiza **Watchtower** en la Mac. La rúbrica pide **dos ambientes** (dev y prod), **UAT** y **cobertura** en el pipeline del backend, **aprobación** antes de prod y un **repo separado de Terraform** (`trading_maxxing-infra`). El estado de cada criterio está en [`docs/rubrica.md`](../rubrica.md) §4; los deploys a Cloudflare son los issues #27 y #45.

## Resumen

| Evento | Workflow | Qué hace |
|---|---|---|
| PR a `develop` o `main` | [`backend.yml`](../../.github/workflows/backend.yml) + [`frontend.yml`](../../.github/workflows/frontend.yml) | Python (`ruff`, `pytest --cov` ≥ 70 %), API Worker (`tsc`, `vitest --coverage` ≥ 70 % con JUnit, `wrangler deploy --dry-run`, nombres de Worker/D1 iguales a Terraform), build sin publicar de las imágenes, Web (`eslint`, `tsc`, `vitest`, export estático) y **E2E mocked** con Playwright (`pnpm e2e:mocked`). |
| Push a `develop` | los mismos | CI completo + imágenes del motor a Docker Hub (`sha-<corto>` y `dev`) + **deploy automático a dev** (migraciones de D1 `tm-dev` y `wrangler deploy --env dev` → `tm-api-dev`; `wrangler pages deploy` a `tm-web-dev`) + **UAT** con Playwright ([`uat.yml`](../../.github/workflows/uat.yml)). |
| Push a `main` | los mismos + [`uat-gate.yml`](../../.github/workflows/uat-gate.yml) | Gate de UAT + **espera la aprobación** del environment `prod` (*required reviewer*) y despliega Worker `tm-api` + D1 `tm-prod` y Pages `tm-web`; imágenes `sha-<corto>` y `prod` a Docker Hub. |
| Semanal / manual | [`cleanup-branches.yml`](../../.github/workflows/cleanup-branches.yml) | Limpia ramas ya fusionadas. |

Los workflows **fallan en verde** cuando faltan credenciales externas (Docker Hub, Cloudflare): dejan un aviso en el *job summary* y terminan `success`, en vez de bloquear PRs mientras no haya cuenta.

## CI (`backend.yml` y `frontend.yml`)

Corre en cada PR y en cada push a `develop` y `main`. Jobs en paralelo:

### Python (motor)

- `uv sync --all-packages --locked` instala el workspace.
- `uv run ruff check .` y `uv run ruff format --check .`.
- `uv run pytest --cov` con **cobertura mínima de 70 %** (`pyproject.toml`); `coverage.xml` como artefacto.

### API Worker (`apps/api`)

- `pnpm typecheck` (tsc estricto).
- `pnpm test:coverage`: vitest **dentro del runtime de Workers con D1 local** (`@cloudflare/vitest-plugin`), cobertura istanbul ≥ 70 % y `reports/junit.xml`. Es lo que se enseña en la demo como "resultado de las pruebas unitarias".
- `pnpm build` = `wrangler deploy --dry-run`: empaqueta el Worker sin publicarlo.

### Web (`apps/web`)

- `pnpm lint`, `pnpm typecheck`, `pnpm test` (Vitest).
- `pnpm build` genera el export estático en `apps/web/out/` y `pnpm check:export` verifica que todas las rutas tengan su HTML.
- Job **E2E mocked (Playwright)**: `pnpm e2e:mocked` levanta `next dev` y recorre onboarding → bot → trade → dashboard con `/api/*` mockeado.

El E2E contra el stack local **real** (API + motor en Docker) es `make e2e-local`; corre en la Mac ([`docs/correr-local.md`](../correr-local.md)).

### Docker

Matriz con las imágenes (`agents`, `engine`, `forecast`): en PR, `docker build` multi-stage amd64 sin publicar con cache de GHA; en push, multi-arch y push a Docker Hub (abajo).

## Deploy del backend — Worker + D1 (#27)

| Trigger | Ambiente | Qué se despliega | Aprobación |
|---|---|---|---|
| Push a `develop` | `dev` | `wrangler d1 migrations apply DB --env dev --remote` + `wrangler deploy --env dev` → `tm-api-dev` | automática |
| Push a `main` | `prod` | mismas migraciones contra `tm-prod` + `wrangler deploy` → `tm-api` | **required reviewer** en el environment `prod` |

Pasos:

1. `CLOUDFLARE_API_TOKEN` y `CLOUDFLARE_ACCOUNT_ID` del environment (la "service connection" de la rúbrica). Si faltan, aviso y verde.
2. Migraciones de D1 antes del deploy (son idempotentes y versionadas en `apps/api/migrations/`).
3. `wrangler deploy` y verificación de `GET /api/health`.
4. **UAT con Playwright** contra la URL de dev (issue [#28](https://github.com/CarloSantoro21/trading_maxxing/issues/28)).
5. Rollback: `wrangler rollback` del Worker.

## Motor — Docker Hub + Watchtower

- El job `docker` de `backend.yml` construye **multi-arch** (`linux/amd64` + `linux/arm64`) `trading-maxxing-{agents,engine,forecast}`.
- Tags: push a `develop` → `sha-<corto>` + `dev`; push a `main` → `sha-<corto>` + `prod`. Son los que sigue [`docker-compose.runner.yml`](../../docker-compose.runner.yml) con `TM_ENV=dev|prod`.
- En la Mac, **Watchtower** vigila el canal configurado y reinicia solo el servicio cuya imagen cambió. El "deploy" del motor es, entonces, publicar la imagen.
- Sin `DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN` el job construye sin publicar y lo avisa.
- Rollback: en `.env.runner`, `TM_ENV=sha-<anterior>` y `make runner-up` (o mover el tag `prod` con `docker buildx imagetools create`).

## Deploy del frontend — Cloudflare Pages (#45)

| Trigger | Proyecto | Aprobación |
|---|---|---|
| Push a `develop` | `tm-web-dev` (rama de producción `develop`) | automática |
| Push a `main` | `tm-web` (rama de producción `main`) | **required reviewer** en el environment `prod` |
| Manual (`workflow_dispatch`) | el que se elija | según ambiente |

- `pnpm check:export` corre antes del deploy: si falta alguna ruta, aborta.
- `wrangler pages deploy out/ --project-name <proyecto> --branch <rama>`.
- La web llama a `/api/*` relativo; Pages lo reenvía al Worker por el **service binding `API`**. No hay URLs del backend ni llaves en el bundle; solo el ambiente y el SHA para el indicador del header.

## Infraestructura — `trading_maxxing-infra`

El Terraform vive en un **repo separado**, [`trading_maxxing-infra`](https://github.com/CarloSantoro21/trading_maxxing-infra), con provider **`cloudflare/cloudflare`** (issues [#26](https://github.com/CarloSantoro21/trading_maxxing/issues/26), [#39](https://github.com/CarloSantoro21/trading_maxxing/issues/39) y [#109](https://github.com/CarloSantoro21/trading_maxxing/issues/109)). Dos stacks (`envs/dev` y `envs/prod`) crean:

- La base **D1** (`tm-dev` / `tm-prod`).
- El proyecto de **Pages** (`tm-web-dev` / `tm-web`) con el service binding `API` al Worker.
- **Access**: la app web (allowlist + One-time PIN) y la app de `/internal/*` con el *service token* del motor.

Outputs: `d1_database_id`, `pages_project_name`, `access_team_domain`, `access_web_aud`, `motor_service_token_client_id` / `motor_service_token_client_secret`. El Worker en sí lo publica `wrangler` desde este repo, no Terraform. El `terraform.yml` del monorepo (Terraform de AWS en `infra/`) es histórico y se retira.

## Flujo de releases

```
feature/<issue>-<descripcion> ──(squash)──► develop ──(PR)──► main
                                               │                │
                                               ▼                ▼
                                    deploy dev automático   aprobación environment prod
                                    tm-api-dev + tm-web-dev  ──► tm-api + tm-web
                                    + imágenes develop       + imágenes X.Y.Z
                                    + UAT (Playwright)
```

1. **Feature** sale de `develop` con el número del issue (`feature/7-wiki-cloudflare`).
2. PR a `develop` con el **CI en verde**. Se fusiona con **squash merge**.
3. Al mergear a `develop`, el pipeline publica las imágenes y **despliega a dev**; después corre la **UAT** contra la URL de dev.
4. Para una entrega se abre PR `develop` → `main`. Al mergear, el job de prod **espera la aprobación** del environment `prod`.
5. **Hotfixes** urgentes: `hotfix/<descripcion>` sale de `main`, regresa a `main` y a `develop`.

Como `develop` no es la rama por defecto del repo, `Closes #N` no cierra los issues automáticamente. Se cierran a mano al fusionar.

### Tests y cobertura

- **Python**: `pytest` con **cobertura mínima 70 %**. Suite completa: `uv run pytest --cov`.
- **API**: vitest en el runtime de Workers con D1 local, cobertura ≥ 70 % y JUnit: `cd apps/api && pnpm test:coverage`.
- **Web**: Vitest + React Testing Library: `cd apps/web && pnpm test`.
- **UAT (E2E)**: Playwright contra la URL de dev después del deploy (issue [#28](https://github.com/CarloSantoro21/trading_maxxing/issues/28)). Con la cobertura, cuenta para los 10 pts del criterio "pipeline del backend".

### Qué **no** corre automáticamente

- `terraform apply`: lo hace el repo [`trading_maxxing-infra`](https://github.com/CarloSantoro21/trading_maxxing-infra).
- Alta de usuarios: un admin con `POST /api/admin/users` y su correo en la allowlist de Access.
- Ensayo de la demo: actividad del equipo, fuera del pipeline.
