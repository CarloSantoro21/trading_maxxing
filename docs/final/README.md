# Documentación final (#30)

Guía de cierre del proyecto **trading_maxxing** para la entrega académica (ITESO, Infraestructura para el Desarrollo Continuo, otoño 2026). Reúne en un solo lugar la arquitectura, el setup local, el pipeline, las decisiones clave y el guion de la demo.

No duplica contenido: para **estado del proyecto, pendientes y resultados** se liga a [`docs/estado-del-proyecto.md`](../estado-del-proyecto.md). Para el **diseño detallado** se liga a la [propuesta](../propuesta/Propuesta-trading_maxxing.md) y al [orquestador](../arquitectura/orquestador.md).

## Contenido

| Archivo | Qué contiene |
|---|---|
| [arquitectura.md](arquitectura.md) | Vista end-to-end: flujo ingest → orquestador → engine → venue → frontend, ciclo de vida de estrategias, persistencia y secretos. |
| [setup-local.md](setup-local.md) | Cómo levantar todo desde cero en una Mac/Linux: prerrequisitos, `.env`, Make, Docker Compose, seed y verificación. |
| [pipeline.md](pipeline.md) | CI/CD actual: qué corre en PR, qué al merge, cobertura, imágenes y flujo de releases. |
| [decisiones.md](decisiones.md) | Bitácora ADR-lite de las decisiones importantes y por qué se tomaron. |
| [demo-guion.md](demo-guion.md) | Guion de 10 min para la demo: storyboard, pantallas, fallas previstas y preguntas. |

## Fuera de alcance de este documento

- La **Wiki** de GitHub (issue [#7](https://github.com/CarloSantoro21/trading_maxxing/issues/7)): la sincronización con la Wiki no se hace aquí.
- El **ensayo en vivo de la demo** (último criterio del issue [#30](https://github.com/CarloSantoro21/trading_maxxing/issues/30)): es una actividad de equipo, no un entregable de código. El guion queda listo en [demo-guion.md](demo-guion.md) para ejecutarlo.
