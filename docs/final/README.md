# Documentación final (#30)

Guía de cierre del proyecto **trading_maxxing** para la entrega académica (ITESO, Infraestructura para el Desarrollo Continuo, otoño 2026). Reúne en un solo lugar la arquitectura, el setup local, el pipeline, las decisiones clave y el guion de la demo.

Alineada con la **[rúbrica oficial del curso](../rubrica.md)** (PR #104): la nube es **Azure App Service + Cloudflare Pages** (ver [`decisiones.md`](decisiones.md) §11) y el guion de la demo sigue el **orden de evaluación** de la sección 2 de la rúbrica.

No duplica contenido: para **estado del proyecto, pendientes y resultados** se liga a [`docs/estado-del-proyecto.md`](../estado-del-proyecto.md). Para el **diseño detallado** se liga a la [propuesta](../propuesta/Propuesta-trading_maxxing.md) y al [orquestador](../arquitectura/orquestador.md).

## Contenido

| Archivo | Qué contiene |
|---|---|
| [arquitectura.md](arquitectura.md) | Vista end-to-end: flujo ingest → orquestador → engine → venue → frontend, ciclo de vida, persistencia, **despliegue en Azure App Service + Cloudflare Pages** y secretos. |
| [setup-local.md](setup-local.md) | Cómo levantar todo desde cero en una Mac/Linux: prerrequisitos, `.env`, Make, Docker Compose, seed y verificación. |
| [pipeline.md](pipeline.md) | CI/CD actual: PR, deploy automático a **dev** y deploy a **prod** con aprobación, cobertura, UAT, Docker Hub y el repo de Terraform `trading_maxxing-infra`. |
| [decisiones.md](decisiones.md) | Bitácora ADR-lite, incluida la **migración AWS → Azure** del 17-nov-2026 (ADR #11, supersede #4). |
| [demo-guion.md](demo-guion.md) | Guion de **10–12 min** con los **10 pasos** del orden de evaluación de la rúbrica, Plan B incluido. |

## Fuera de alcance de este documento

- La **Wiki** de GitHub (issue [#7](https://github.com/CarloSantoro21/trading_maxxing/issues/7)): la sincronización con la Wiki no se hace aquí.
- El **ensayo en vivo de la demo** (último criterio del issue [#30](https://github.com/CarloSantoro21/trading_maxxing/issues/30)): es una actividad de equipo, no un entregable de código. El guion queda listo en [demo-guion.md](demo-guion.md) para ejecutarlo.
- El **código de la infraestructura**: vive en el repo [`trading_maxxing-infra`](https://github.com/CarloSantoro21/trading_maxxing-infra) (Terraform con `azurerm`), no en este monorepo.
