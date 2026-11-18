# Arquitectura

Esta página es el hub de los diagramas de arquitectura de trading_maxxing. Cada sección vive en su propia página de Wiki para seguir la rúbrica (`docs/rubrica.md`, PR #104), que pide páginas específicas para componentes, infraestructura y deployment:

- [Diagrama de clases](Diagrama-de-clases): componentes y métodos del sistema.
- [Stack tecnológico](Stack-tecnologico): tecnologías elegidas y por qué.
- [Infraestructura (Azure + Cloudflare)](Infraestructura): recursos de Azure que se crean con Terraform y el frontend en Cloudflare Pages.
- [Deployment y CI/CD](Deployment): pipeline GitHub Actions → Docker Hub → Azure App Service (dev/prod) + Cloudflare Pages (dev/prod).
- [Estrategia de ramas](Estrategia-de-ramas): Git Flow simplificado con `feature/* → develop → main`.

El código fuente Mermaid de los diagramas vive en [`docs/propuesta/`](https://github.com/CarloSantoro21/trading_maxxing/tree/main/docs/propuesta) del repositorio. Los diagramas de infraestructura y de deployment están pendientes de regeneración con íconos de Azure/Cloudflare (ver [Azure icons](https://learn.microsoft.com/azure/architecture/icons/)); mientras tanto, cada página incluye una versión en Mermaid que sí refleja la arquitectura nueva.

## Orquestador: capa compartida y capa por usuario

```
                 ┌──────────────── capa compartida (una vez) ────────────────┐
 velas cerradas ─▶ estrategia viva → Signal → veto de noticias → SimulationGate │──▶ tm:signals
 (MarketData)    └────────────────────────────────────────────────────────────┘
                                                                     │
                                  ReferenceService y LifecycleMonitor│  ledger de referencia
                 ┌──────────── capa por usuario (un BotRunner por bot) ───────┐
 tm:commands ───▶│ peso del sleeve × exposición del nivel × confianza          │──▶ tm:decisions
 (API)           │ → RiskAgent con los límites del nivel                       │──▶ tm:shadow
                 └────────────────────────────────────────────────────────────┘
                                                                     │
                                         engine (AgenticStrategy) ◀──┘  órdenes por bot y sleeve
```

La capa compartida calcula señales, vetos y simulación una sola vez para todos los usuarios. La capa por usuario (un `BotRunner` por bot en `running`) aplica el peso global de cada estrategia y la exposición del nivel de riesgo del usuario.

El detalle vive en [`docs/arquitectura/orquestador.md`](https://github.com/CarloSantoro21/trading_maxxing/blob/main/docs/arquitectura/orquestador.md): formato de `tm:signals`, consumidores, curvas de sensibilidad del nivel de riesgo, LifecycleMonitor, modo shadow, comandos y cómo se reinician las posiciones desde la tabla `trades`.
