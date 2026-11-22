# Arquitectura

Esta página es el hub de los diagramas de arquitectura de trading_maxxing. Cada sección vive en
su propia página, como pide la rúbrica (`docs/rubrica.md`):

- [Diagrama de clases](Diagrama-de-clases): componentes y métodos del sistema.
- [Stack tecnológico](Stack-tecnologico): tecnologías elegidas y por qué.
- [Infraestructura (Cloudflare + Mac)](Infraestructura): recursos de Cloudflare que crea Terraform y el motor en la Mac.
- [Deployment y CI/CD](Deployment): GitHub Actions → Cloudflare (Workers, D1, Pages) dev/prod + Docker Hub → Mac.
- [Estrategia de ramas](Estrategia-de-ramas): Git Flow simplificado con `feature/* → develop → main`.

## Arquitectura final (21 de noviembre de 2026)

```
Navegador ─▶ Cloudflare Access ─▶ Pages (Next.js estático) ─/api/* service binding─▶ Worker (Hono) ─▶ D1
                                                                                       ▲ /internal/*
Mac de Carlo (docker compose): agents · engine (NautilusTrader) · forecast (Kronos) · Redis
   └── solo conexiones salientes: Binance Spot Testnet y la API del Worker
```

- **Cloudflare, plan gratuito:** Pages, Workers + Hono, D1, Access y Workers Observability.
  Sin Supabase.
- **Motor en la Mac:** imágenes de Docker Hub, Watchtower las actualiza.
- **LLM opcional:** noticias y explicaciones apagadas por defecto.

Detalle, por qué el motor no va en Workers y el plan con Cloudflare Containers:
[`docs/arquitectura/cloudflare.md`](https://github.com/CarloSantoro21/trading_maxxing/blob/develop/docs/arquitectura/cloudflare.md).
Contrato de la API: [`docs/arquitectura/api.md`](https://github.com/CarloSantoro21/trading_maxxing/blob/develop/docs/arquitectura/api.md).

Los diagramas de infraestructura y de deployment se generan con la librería `diagrams` desde
[`docs/propuesta/`](https://github.com/CarloSantoro21/trading_maxxing/tree/develop/docs/propuesta)
(`infra.py` y `deploy.py`); cada página incluye además una versión en Mermaid.

## Orquestador: capa compartida y capa por usuario

```
                 ┌──────────────── capa compartida (una vez) ────────────────┐
 velas cerradas ─▶ estrategia viva → Signal → veto de noticias → SimulationGate │──▶ tm:signals
 (MarketData)    └────────────────────────────────────────────────────────────┘
                                                                     │
                                  ReferenceService y LifecycleMonitor│  ledger de referencia
                 ┌──────────── capa por usuario (un BotRunner por bot) ───────┐
 comandos de ───▶│ peso del sleeve × exposición del nivel × confianza          │──▶ tm:decisions
 la API          │ → RiskAgent con los límites del nivel                       │──▶ tm:shadow
                 └────────────────────────────────────────────────────────────┘
                                                                     │
                                         engine (AgenticStrategy) ◀──┘  órdenes por bot y sleeve
```

La capa compartida calcula señales, vetos y simulación una sola vez para todos los usuarios. La
capa por usuario (un `BotRunner` por bot en `running`) aplica el peso global de cada estrategia
y la exposición del nivel de riesgo del usuario. Los comandos (`start`, `stop`, `kill`) los
escribe la API en la tabla `commands` de D1 y el motor los lee con `GET /internal/commands`.

El detalle vive en [`docs/arquitectura/orquestador.md`](https://github.com/CarloSantoro21/trading_maxxing/blob/develop/docs/arquitectura/orquestador.md).
