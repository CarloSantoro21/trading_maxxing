# Cómo contribuir a trading_maxxing

## Ramas (Git Flow simplificado)

| Rama | Uso | Sale de | Regresa a |
|---|---|---|---|
| `main` | Producción (ambiente **prod**: Pages `tm-web`, Worker `tm-api`, D1 `tm-prod`). Cada push despliega tras la aprobación del environment `prod`. Cada release lleva un tag `vX.Y.Z` | — | — |
| `develop` | Integración (ambiente **dev**: Pages `tm-web-dev`, Worker `tm-api-dev`, D1 `tm-dev`). Cada push despliega automático y corre la UAT | `main` | `main` (PR de promoción) |
| `feature/<issue>-<descripcion>` | Funcionalidades, p. ej. `feature/18-risk-agent` | `develop` | `develop` (squash merge) |
| `release/X.Y.Z` | Estabilización antes de liberar; solo correcciones | `develop` | `main` y `develop` |
| `hotfix/<descripcion>` | Correcciones urgentes en producción | `main` | `main` y `develop` |

`main` y `develop` están protegidas: no se hace push directo, todo entra por pull request con el CI en verde (detalle en [Protección de ramas](#protección-de-ramas)).

Promoción a producción: PR `develop` → `main` con **merge commit** (así el gate de UAT encuentra el commit de `develop` que se probó en dev). El deploy a prod espera la aprobación del environment `prod`. El flujo completo, los secrets y el rollback están en [docs/pipeline-y-ambientes.md](docs/pipeline-y-ambientes.md).

## Plan de trabajo y ramas

### Jerarquía PBI → Story → Task

El trabajo se planea en el [Project Board](https://github.com/users/CarloSantoro21/projects/2) con tres niveles, unidos con los **sub-issues** de GitHub:

| Nivel | Etiqueta | Qué es | Ejemplo |
|---|---|---|---|
| **PBI** (épica) | `pbi` | Objetivo grande del producto; agrupa stories. Cada milestone tiene uno o dos PBI | #126 Motor de trading, datos y pronóstico |
| **Story** | `story` | Funcionalidad entregable con valor para el usuario; es sub-issue de su PBI | #8 Servicio forecast: wrapper de Kronos |
| **Task** | `task` | Trabajo técnico concreto (pruebas, configuración, refactor); es sub-issue de su story o, si no hay una story natural, de su PBI | #9 Pruebas unitarias de KronosForecastService |

PBI actuales:

| PBI | Milestone | Agrupa |
|---|---|---|
| #125 Fundaciones, CI/CD y ambientes | M1 | Monorepo, Dockerfiles, compose, pipelines y UAT |
| #126 Motor de trading, datos y pronóstico | M2 | NautilusTrader, datos de mercado, backtests, Kronos y motor en la Mac |
| #127 Laboratorio y ciclo de vida de estrategias | M2 | Estrategias, Monte Carlo, registro, modo shadow y degradación |
| #128 Agentes y orquestador | M3 | Agentes, orquestador por sleeves, SimulationGate y LLM |
| #129 API y base de datos en Cloudflare | M3 | API de perfiles, bots y trades, usuarios y Worker con D1 |
| #130 Frontend del dashboard | M4 | Auth, dashboard, backtests, metas y estrategias |
| #131 Infraestructura como código y monitoreo | M5 | Terraform de Cloudflare, estado remoto, Access y APM |
| #132 Documentación, plan de trabajo y demo | M5 | Wiki, diagramas, plan de trabajo y presentación |

Reglas:

- Todo issue nuevo lleva **una** etiqueta de tipo (`story` o `task`), sus etiquetas de área, un milestone y un padre (sub-issue de un PBI o de una story).
- En el board, el campo **Tipo** (PBI / Story / Task) repite la etiqueta para poder filtrar y agrupar; el campo **Parent issue** y la barra **Sub-issues progress** muestran el avance de cada PBI.
- Las ramas y los PRs se nombran con el número de la story o task (`feature/<issue>-<descripcion>`) y el PR la enlaza con `Closes #N`, así cada commit queda ligado al plan.
- Para ver la jerarquía: abre un PBI y revisa la sección *Sub-issues*, o en el board agrupa por **Parent issue** o por **Milestone**.

### Protección de ramas

`main` y `develop` están protegidas en este repo y en [`trading_maxxing-infra`](https://github.com/CarloSantoro21/trading_maxxing-infra) con las mismas reglas:

| Regla | Valor |
|---|---|
| Pull request obligatorio (sin push directo) | Sí |
| Aprobaciones requeridas | 0 (no bloquea al equipo; las revisiones se piden en el PR) |
| Descartar aprobaciones viejas al subir commits | Sí |
| Conversaciones resueltas antes del merge | Sí |
| Force push | Prohibido |
| Borrar la rama | Prohibido |
| Aplicar a administradores (`enforce_admins`) | No, para que el dueño del repo pueda intervenir en una emergencia |

Checks requeridos (deben estar en verde para hacer merge):

| Repo | Checks |
|---|---|
| `trading_maxxing` | `Python (ruff + pytest)` y `API Worker (tsc + vitest + build)` del workflow `Backend`; `Web (eslint + tsc + vitest)` del workflow `Frontend` |
| `trading_maxxing-infra` | `fmt + validate + test (dev)` y `fmt + validate + test (prod)` del workflow `Terraform` |

Los workflows `Backend` y `Frontend` corren en **todo** PR a `develop` o `main` (el filtro de rutas solo aplica en push), así que los checks siempre aparecen, aunque el PR solo cambie documentación.

Si cambia el nombre de un job, hay que actualizar la protección (Settings → Branches, o `gh api -X PUT repos/<owner>/<repo>/branches/<rama>/protection`), porque un check requerido que ya no existe deja el PR esperando para siempre.

## Commits: Conventional Commits

```
<tipo>(<alcance opcional>): <descripción en minúsculas>
```

Tipos: `feat`, `fix`, `test`, `ci`, `docs`, `refactor`, `chore`, `perf`. Ejemplos:

- `feat(agents): TrendAgent con canales Donchian`
- `fix(engine): cantidad mínima de la orden en Binance`
- `ci: cobertura mínima de 70 % en pytest`

## Pull requests

1. Crea la rama desde `develop` con el número del issue.
2. Usa la plantilla del PR y enlaza el issue (`Closes #N`).
3. El título del PR sigue Conventional Commits, porque se usa como mensaje del squash merge.
4. El CI debe pasar (workflows `Backend` y `Frontend`): lint, tipos, pruebas y cobertura ≥ 70 % en Python y en la API, y el E2E con la API mockeada (`pnpm e2e:mocked`). En local: `make lint test`; si tocaste el flujo bot → motor → dashboard, corre también `make e2e-local` ([docs/correr-local.md](docs/correr-local.md)).
5. Merge con **squash** a `develop`.

## Versiones

Versionado semántico. Al liberar: `release/X.Y.Z` → merge a `main` → tag `vX.Y.Z` → deploy a producción con aprobación manual.
