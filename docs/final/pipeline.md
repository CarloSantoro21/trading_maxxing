# CI/CD

Estado de los pipelines. Las fuentes son los workflows en [`.github/workflows/`](../../.github/workflows), la guía de contribución en [`CONTRIBUTING.md`](../../CONTRIBUTING.md) y la [rúbrica oficial del curso](../rubrica.md).

> **Nota (17-nov-2026).** La rúbrica exige **dos ambientes** por repo (dev y prod), despliegue a **Azure App Service** (backend) y **Cloudflare Pages** (frontend), **UAT** (Playwright) y **cobertura** en el pipeline del backend, y un **repo separado de Terraform** (`trading_maxxing-infra`). El estado actual de cada criterio está en [`docs/estado-del-proyecto.md`](../estado-del-proyecto.md).

## Resumen

| Evento | Workflow | Qué hace |
|---|---|---|
| PR a `develop` o `main` | [`ci.yml`](../../.github/workflows/ci.yml) | Lint, tipos, pruebas unitarias con **cobertura ≥ 70 %**, build (sin publicar) de las 5 imágenes. |
| PR que toca `supabase/` | [`db.yml`](../../.github/workflows/db.yml) | `supabase db start` + `supabase test db` (pgTAP). |
| PR que toca `infra/` | [`terraform.yml`](../../.github/workflows/terraform.yml) | `fmt`, `init -backend=false`, `validate`, `tflint`. *(Se retira cuando la infra viva solo en `trading_maxxing-infra`.)* |
| Push a `develop` | `ci.yml` + `docker-publish.yml` + deploy dev | CI completo + build multi-arch y push a Docker Hub (`develop`, `sha-<commit>`) + **deploy automático al ambiente dev** (backend en Azure App Service, frontend en Cloudflare Pages) + **UAT** con Playwright. |
| Push a `main` | `docker-publish.yml` + deploy prod | Build multi-arch y push a Docker Hub (`X.Y.Z`, `latest`) + **deploy a prod con aprobación manual** (environment `prod`, *required reviewer*). |
| Manual | [`deploy-web.yml`](../../.github/workflows/deploy-web.yml) | Deploy del frontend a Cloudflare Pages con elección de ambiente (dev o prod). |
| Semanal / manual | [`cleanup-branches.yml`](../../.github/workflows/cleanup-branches.yml) | Limpia ramas ya fusionadas. |

Los workflows están diseñados para **fallar en verde** cuando faltan credenciales externas (Docker Hub, Cloudflare, Azure): dejan un aviso en el *job summary* y terminan `success`, en vez de bloquear PRs mientras no haya cuenta.

## CI principal (`ci.yml`)

Corre en cada PR y en cada push a `develop` y `main`. Tres jobs en paralelo:

### Python

- `uv sync --all-packages --locked` instala el workspace.
- `uv run ruff check .` y `uv run ruff format --check .` como lint y formato.
- `uv run pytest --cov --cov-report=term --cov-report=xml` con **cobertura mínima de 70 %** (configurada en `pyproject.toml`). El archivo `coverage.xml` se sube como artefacto. **La rúbrica pide cobertura visible en el run** (10 pts para "UAT y cobertura" en el pipeline del backend).

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

## Infraestructura — `trading_maxxing-infra`

El Terraform vive en un **repo separado**, [`trading_maxxing-infra`](https://github.com/CarloSantoro21/trading_maxxing-infra), con provider **`azurerm`** (ver issues [#26](https://github.com/CarloSantoro21/trading_maxxing/issues/26) y [#39](https://github.com/CarloSantoro21/trading_maxxing/issues/39)). Dos stacks (`envs/dev` y `envs/prod`) crean:

- Resource Group por ambiente.
- App Service Plan **B1** compartido para `api` y `agents` (Linux, contenedores).
- Web Apps (`api`, `agents`) con imagen desde Docker Hub.
- Application Insights + Log Analytics Workspace.
- Azure Key Vault con los secretos (Supabase, Binance Testnet, Groq, CryptoPanic).
- Azure Cache for Redis Basic C0.

El workflow del repo de infra corre `terraform fmt`, `validate`, `plan` en cada PR (con OIDC federado hacia Azure) y `apply` al mergear a `main`. El `terraform.yml` que todavía queda en el monorepo (para el Terraform de AWS en `infra/`) se retira en cuanto la migración termine.

## Docker Hub (`docker-publish.yml`)

- Matriz con las 5 imágenes, build **multi-arch** (`linux/amd64` + `linux/arm64`) con QEMU y buildx.
- Tags por evento:
  - Push a `develop`: `develop` + `sha-<commit>`.
  - Tag `vX.Y.Z` en `main`: `X.Y.Z` + `latest`.
  - PR que toca el propio workflow: solo construye.
- Sin `DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN` el job construye sin publicar y lo avisa en el *job summary*.

Las imágenes publicadas se consumen desde **Azure App Service** (backend) y se pueden usar en local con `docker-compose.hub.yml` (ver [`setup-local.md`](setup-local.md) §5, opción C).

## Deploy del backend — Azure App Service (`deploy-api.yml`, `deploy-agents.yml`)

Dos ambientes, **dev** y **prod**, materializados como *GitHub Environments*:

| Trigger | Ambiente | Imagen desplegada | Aprobación |
|---|---|---|---|
| Push a `develop` | `dev` | `trading-maxxing-api:sha-<commit>`, `trading-maxxing-agents:sha-<commit>` | automática |
| Push a `main` | `prod` | `trading-maxxing-api:X.Y.Z`, `trading-maxxing-agents:X.Y.Z` | **required reviewer** en el environment `prod` |

Pasos del job:

1. Login a Azure con **OIDC federado** (`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`).
2. `az webapp config container set --docker-custom-image-name <imagen>` para cada Web App.
3. `az webapp restart` y espera a que el `/health` responda 200.
4. **UAT con Playwright** sobre la URL del ambiente (solo en dev, bloquea el merge a `main` si falla). La suite cubre el flujo login → onboarding → bot → trade → dashboard (issue [#28](https://github.com/CarloSantoro21/trading_maxxing/issues/28)).
5. Smoke tests contra la API desplegada (`/health`, `/docs`, un `GET /strategies` autenticado).

El job falla en verde si faltan credenciales de Azure (mismo patrón que Cloudflare).

## Deploy del frontend — Cloudflare Pages (`deploy-web.yml`)

Dos ambientes, **dev** y **prod**, como proyectos de Cloudflare Pages separados:

| Trigger | Proyecto | Branch en Cloudflare | Aprobación |
|---|---|---|---|
| Push a `develop` | `trading-maxxing-dev` | `develop` | automática |
| Push a `main` | `trading-maxxing-prod` | `main` | **required reviewer** en el environment `prod` |
| Manual (`workflow_dispatch`) | el que se elija | el que se elija | según ambiente |

- Verifica `CLOUDFLARE_API_TOKEN` y `CLOUDFLARE_ACCOUNT_ID`; si faltan, deja aviso y termina en verde.
- Variables del build (`NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_API_URL`) se incrustan en el bundle: **cada ambiente requiere un build propio** (apunta a su backend en Azure).
- `pnpm check:export` corre antes del push: si falta alguna ruta el deploy aborta.
- `wrangler pages deploy out/ --project-name <proyecto> --branch <rama>`.

## Flujo de releases

El flujo de ramas (ver [`CONTRIBUTING.md`](../../CONTRIBUTING.md)) es Git Flow simplificado:

```
feature/<issue>-<descripcion>   ──(squash)──► develop   ──(release/X.Y.Z + merge)──► main ──(tag vX.Y.Z)──►
                                     │                                                    │
                                     ▼                                                    ▼
                               deploy dev auto                                     deploy prod (aprobación)
                           + UAT en Azure dev + web dev                        + Azure prod + web prod
```

1. **Feature** sale de `develop` con el número del issue (`feature/30-docs-final`).
2. Se abre PR a `develop` con el **CI en verde** (ruff, pytest ≥ 70 %, eslint, tsc, vitest, build de las 5 imágenes). Si toca `supabase/` también corre `db.yml`. El PR se fusiona con **squash merge**.
3. Al mergear a `develop`, el pipeline publica a Docker Hub (`sha-<commit>` y `develop`) y **despliega automáticamente al ambiente dev** (Azure App Service dev + Cloudflare Pages dev). Después corre la **UAT (Playwright)** contra la URL dev.
4. Para una entrega se abre `release/X.Y.Z` desde `develop` para estabilizar. Se mergea a `main` con **merge commit** y a `develop` para no perder los parches.
5. En `main` se crea el tag `vX.Y.Z`. El pipeline publica `X.Y.Z`/`latest` a Docker Hub y **espera aprobación** del *environment* `prod` para desplegar a Azure App Service prod y a Cloudflare Pages prod.
6. **Hotfixes** urgentes: `hotfix/<descripcion>` sale de `main`, regresa a `main` y a `develop`.

Como `develop` no es la rama por defecto del repo, `Closes #N` no cierra los issues automáticamente. Se cierran a mano al fusionar.

### Tests y cobertura

- **Python**: `pytest` con **cobertura mínima 70 %** (configurada en `pyproject.toml`). Suite completa: `uv run pytest --cov`. La cobertura cuenta para la rúbrica (criterio "pipeline del backend", 10 pts: UAT **y** cobertura).
- **Web**: Vitest + React Testing Library para formularios y componentes del dashboard. Comando: `pnpm test` dentro de `apps/web/`.
- **Base de datos**: pgTAP en `supabase/tests/`, correlo con `supabase test db`.
- **UAT (E2E)**: Playwright sobre la URL del ambiente dev después del deploy (issue [#28](https://github.com/CarloSantoro21/trading_maxxing/issues/28)). Flujo: login → onboarding → bot → trade → dashboard. La UAT también cuenta para los 10 pts del criterio "pipeline del backend".

### Qué **no** corre automáticamente

- Terraform `apply`: lo hace el workflow del repo [`trading_maxxing-infra`](https://github.com/CarloSantoro21/trading_maxxing-infra), al mergear a `main` de ese repo.
- Ensayo de la demo: actividad del equipo, fuera del pipeline.
