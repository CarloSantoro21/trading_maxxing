# Project Board

El plan de trabajo, los issues y su estado se siguen en el GitHub Project Board del repositorio.

- **Project Board:** https://github.com/users/CarloSantoro21/projects/2
- **Issues:** https://github.com/CarloSantoro21/trading_maxxing/issues
- **Vista alternativa (pestaña de Projects del repo):** https://github.com/CarloSantoro21/trading_maxxing/projects *(actualizar al enlace real del tablero si cambia la organización del repo)*

## Columnas

- **Backlog**: ideas y temas por priorizar.
- **To Do**: ya priorizados y listos para tomarse.
- **In Progress**: alguien está trabajando en ellos.
- **In Review**: pull request abierto y en revisión.
- **Done**: fusionados a `develop` o `main`.

## Convenciones

- Cada issue lleva descripción, criterios de aceptación, etiquetas por área (`frontend`, `backend`, `agents`, `ml`, `trading-engine`, `devops`, `testing`, `docs`, `database`) y milestone.
- Las ramas nacen como `feature/<issue>-<descripcion>` desde `develop`.
- Los pull requests enlazan su issue con `Closes #N` y se fusionan con **squash merge** a `develop`.
- `develop` no es la rama por defecto, así que `Closes #N` **no cierra el issue automáticamente**; hay que cerrarlos a mano al fusionar.

## Milestones

- **M1** Fundaciones e infraestructura — hasta el 11 de noviembre.
- **M2** Motor, pronóstico y estrategias — hasta el 18 de noviembre.
- **M3** Agentes, simulación y API — hasta el 24 de noviembre.
- **M4** Frontend y validación local — hasta el 1 de diciembre.
- **M5** Entrega: deploy y demo — hasta el 6 de diciembre.

El detalle del estado actual (issues cerrados vs. pendientes, decisiones y bloqueos) está en [`docs/estado-del-proyecto.md`](https://github.com/CarloSantoro21/trading_maxxing/blob/main/docs/estado-del-proyecto.md).
