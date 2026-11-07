# Cómo contribuir a trading_maxxing

## Ramas (Git Flow simplificado)

| Rama | Uso | Sale de | Regresa a |
|---|---|---|---|
| `main` | Producción. Cada merge lleva un tag `vX.Y.Z` | — | — |
| `develop` | Integración, desplegada en staging | `main` | — |
| `feature/<issue>-<descripcion>` | Funcionalidades, p. ej. `feature/18-risk-agent` | `develop` | `develop` (squash merge) |
| `release/X.Y.Z` | Estabilización antes de liberar; solo correcciones | `develop` | `main` y `develop` |
| `hotfix/<descripcion>` | Correcciones urgentes en producción | `main` | `main` y `develop` |

`main` y `develop` están protegidas: no se hace push directo, todo entra por pull request con el CI en verde.

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
4. El CI debe pasar: lint, pruebas y cobertura de Python ≥ 70 %.
5. Merge con **squash** a `develop`.

## Versiones

Versionado semántico. Al liberar: `release/X.Y.Z` → merge a `main` → tag `vX.Y.Z` → deploy a producción con aprobación manual.
