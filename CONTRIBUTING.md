# Cómo contribuir a trading_maxxing

## Ramas (Git Flow simplificado)

| Rama | Uso | Sale de | Regresa a |
|---|---|---|---|
| `main` | Producción (ambiente **prod**: Pages `tm-web`, Worker `tm-api`, D1 `tm-prod`). Cada push despliega tras la aprobación del environment `prod`. Cada release lleva un tag `vX.Y.Z` | — | — |
| `develop` | Integración (ambiente **dev**: Pages `tm-web-dev`, Worker `tm-api-dev`, D1 `tm-dev`). Cada push despliega automático y corre la UAT | `main` | `main` (PR de promoción) |
| `feature/<issue>-<descripcion>` | Funcionalidades, p. ej. `feature/18-risk-agent` | `develop` | `develop` (squash merge) |
| `release/X.Y.Z` | Estabilización antes de liberar; solo correcciones | `develop` | `main` y `develop` |
| `hotfix/<descripcion>` | Correcciones urgentes en producción | `main` | `main` y `develop` |

`main` y `develop` están protegidas: no se hace push directo, todo entra por pull request con el CI en verde.

Promoción a producción: PR `develop` → `main` con **merge commit** (así el gate de UAT encuentra el commit de `develop` que se probó en dev). El deploy a prod espera la aprobación del environment `prod`. El flujo completo, los secrets y el rollback están en [docs/pipeline-y-ambientes.md](docs/pipeline-y-ambientes.md).

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
