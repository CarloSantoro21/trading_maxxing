# CI/CD

Estado actual de los pipelines. Las fuentes son los workflows en [`.github/workflows/`](../../.github/workflows) y la guía de contribución en [`CONTRIBUTING.md`](../../CONTRIBUTING.md).

## Resumen

| Evento | Workflow | Qué hace |
|---|---|---|
| PR a `develop` o `main` | [`ci.yml`](../../.github/workflows/ci.yml) | Lint, tipos, pruebas y build (sin publicar) de las 5 imágenes. |
| PR que toca `supabase/` | [`db.yml`](../../.github/workflows/db.yml) | `supabase db start` + `supabase test db` (pgTAP). |
| PR que toca `infra/` | [`terraform.yml`](../../.github/workflows/terraform.yml) | `fmt`, `init -backend=false`, `validate`, `tflint`. Opcional: `plan` si hay credenciales. |
| Push a `develop` | `ci.yml` + `docker-publish.yml` | CI completo + build multi-arch y push a Docker Hub con `develop` y `sha-<commit>`. |
| Tag `vX.Y.Z` en `main` | `docker-publish.yml` | Build multi-arch y push a Docker Hub con `X.Y.Z` y `latest`. |
| Manual | [`deploy-web.yml`](../../.github/workflows/deploy-web.yml) | Deploy del frontend a Cloudflare Pages (requiere secrets). |
| Semanal / manual | [`cleanup-branches.yml`](../../.github/workflows/cleanup-branches.yml) | Limpia ramas ya fusionadas. |

Los workflows están diseñados para **fallar en verde** cuando faltan credenciales externas (Docker Hub, Cloudflare, AWS): dejan un aviso en el *job summary* y terminan `success`, en vez de bloquear PRs mientras no haya cuenta.

## CI principal (`ci.yml`)

Corre en cada PR y en cada push a `develop` y `main`. Tres jobs en paralelo:

### Python

- `uv sync --all-packages --locked` instala el workspace.
- `uv run ruff check .` y `uv run ruff format --check .` como lint y formato.
- `uv run pytest --cov --cov-report=term --cov-report=xml` con **cobertura mínima de 70 %** (configurada en `pyproject.toml`). El archivo `coverage.xml` se sube como artefacto.

### Web

- `pnpm install --frozen-lockfile`.
- `pnpm lint` (ESLint), `pnpm typecheck` (`tsc --noEmit`), `pnpm test` (Vitest).
- `pnpm build` genera el export estático en `apps/web/out/` y `pnpm check:export` verifica que todas las rutas de `src/app` tengan su HTML (y que no haya route handlers o segmentos dinámicos sin `generateStaticParams`).

### Docker

Matriz con las 5 imágenes (`api`, `agents`, `forecast`, `engine`, `web`): `docker build` multi-stage sin publicar, con cache de GHA. Mantiene paridad con el build de producción del workflow `docker-publish`.

## Base de datos (`db.yml`)

Solo corre si cambia algo en `supabase/`. Levanta Postgres con la CLI de Supabase (misma versión que en local, `2.117.0`), aplica migraciones + seed y corre las pruebas pgTAP de `supabase/tests/`:

- Schema, enums, RLS activada, triggers, índices y publicación Realtime.
- Constraints: ranges, símbolos soportados, SL/TP del lado correcto, etc.
- RLS con dos usuarios: A no ve ni modifica lo de B; `anon` no ve nada.
- Ciclo de vida de `strategies`: transiciones permitidas y prohibidas, promociones solo humanas.

## Infraestructura (`terraform.yml`)

Corre en cada PR que toca `infra/` y en pushes a `develop`/`main`. Sin credenciales de AWS:

- `terraform fmt -check -recursive -diff`.
- `terraform init -backend=false` + `validate` por stack (`bootstrap`, `envs/staging`, `envs/prod`).
- `tflint` (sugerencia, no bloquea).

Cuando haya cuenta de AWS (issues [#39](https://github.com/CarloSantoro21/trading_maxxing/issues/39), [#26](https://github.com/CarloSantoro21/trading_maxxing/issues/26)), el job `plan` corre `terraform plan` y pega el resultado como comentario en el PR. Se activa automáticamente al llenar `AWS_PLAN_ROLE_ARN` (OIDC) o los secrets temporales de AWS Academy.

## Docker Hub (`docker-publish.yml`)

- Matriz con las 5 imágenes, build **multi-arch** (`linux/amd64` + `linux/arm64`) con QEMU y buildx.
- Tags por evento:
  - Push a `develop`: `develop` + `sha-<commit>`.
  - Tag `vX.Y.Z`: `X.Y.Z` + `latest`.
  - PR que toca el propio workflow: solo construye.
- Sin `DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN` el job construye sin publicar y lo avisa en el *job summary*.

Las imágenes publicadas se pueden usar directamente sin construir con `docker-compose.hub.yml` (ver [`setup-local.md`](setup-local.md) §5, opción C).

## Deploy del frontend (`deploy-web.yml`)

Workflow **solo manual** (`workflow_dispatch`): el despliegue a Cloudflare Pages está diferido hasta la entrega (issue [#45](https://github.com/CarloSantoro21/trading_maxxing/issues/45)).

- Verifica `CLOUDFLARE_API_TOKEN` y `CLOUDFLARE_ACCOUNT_ID`; si faltan, deja aviso y termina en verde.
- Variables del build (`NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_API_URL`) se incrustan en el bundle: cada entorno requiere un build propio.
- `pnpm check:export` corre antes del push: si falta alguna ruta el deploy aborta.
- `wrangler pages deploy out/ --project-name trading-maxxing --branch <rama>`.

## Flujo de releases

El flujo de ramas (ver [`CONTRIBUTING.md`](../../CONTRIBUTING.md)) es Git Flow simplificado:

```
feature/<issue>-<descripcion>   ──(squash)──► develop   ──(release/X.Y.Z + merge)──► main ──(tag vX.Y.Z)──►
```

1. **Feature** sale de `develop` con el número del issue (`feature/30-docs-final`).
2. Se abre PR a `develop` con el **CI en verde** (ruff, pytest ≥ 70 %, eslint, tsc, vitest, build de las 5 imágenes). Si toca `supabase/` también corre `db.yml`; si toca `infra/`, `terraform.yml`. El PR se fusiona con **squash merge**.
3. Para una entrega se abre `release/X.Y.Z` desde `develop` para estabilizar (solo correcciones). Se mergea a `main` con **merge commit** (no squash) y a `develop` para no perder los parches.
4. En `main` se crea el tag `vX.Y.Z`, que dispara el push de imágenes a Docker Hub con tags `X.Y.Z` y `latest`.
5. **Hotfixes** urgentes: `hotfix/<descripcion>` sale de `main`, regresa a `main` y a `develop`.

Como `develop` no es la rama por defecto del repo, `Closes #N` no cierra los issues automáticamente. Se cierran a mano al fusionar.

### Tests y cobertura

- Python: `pytest` con cobertura mínima **70 %** (configurada en `pyproject.toml`). Suite completa: `uv run pytest --cov`.
- Web: Vitest + React Testing Library para formularios y componentes del dashboard. Comando: `pnpm test` dentro de `apps/web/`.
- Base de datos: pgTAP en `supabase/tests/`, correlo con `supabase test db`.
- E2E: pendiente (issue [#28](https://github.com/CarloSantoro21/trading_maxxing/issues/28), Playwright login → onboarding → bot → trade → dashboard).

### Qué **no** corre automáticamente

- Deploy a AWS (ECS Fargate): el workflow queda planeado dentro de `infra/` cuando haya cuenta (issue [#27](https://github.com/CarloSantoro21/trading_maxxing/issues/27)).
- Deploy a Cloudflare Pages: solo manual (`deploy-web.yml`).
- Ensayo de la demo: actividad del equipo, fuera del pipeline.
