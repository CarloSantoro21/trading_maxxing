# Decisiones del proyecto (ADR-lite)

Bitácora corta de las decisiones importantes del proyecto. No sustituye los PRs ni los issues: enlaza a ellos cuando aporta contexto. Formato ADR-lite: contexto, decisión, consecuencias.

## 1. NautilusTrader como motor de ejecución y backtest

- **Contexto.** Necesitamos un motor event-driven con backtesting determinista, adaptador para Binance, control de riesgo y órdenes bracket. Alternativas: Backtrader, Vectorbt, Zipline, construcción propia.
- **Decisión.** Usar [NautilusTrader](https://github.com/nautechsystems/nautilus_trader) `1.231.0` (LGPL-3.0) como librería. Escribimos `AgenticStrategy` (subclase de `Strategy`) que recibe `TradeDecision` y envía órdenes bracket.
- **Consecuencias.**
  - El mismo código corre en backtest (`BacktestEngine`) y en vivo (`TradingNode`).
  - Núcleo en Rust acelerado; API de Python estable con contratos tipados.
  - Riesgo: la API puede cambiar entre menores. Mitigación: versión y digest fijos.
  - LGPL permite usarlo como dependencia sin modificaciones ni liberar nuestro código bajo la misma licencia.

Referencias: propuesta sección 2.2; [`services/engine/README.md`](../../services/engine/README.md).

## 2. Supabase para Postgres, Auth y Realtime

- **Contexto.** Necesitamos Postgres, login, un canal de eventos para el dashboard y RLS para aislar usuarios. Alternativas: Firebase, Postgres crudo + custom auth + WebSocket propio, Hasura.
- **Decisión.** Supabase local con su CLI durante desarrollo (sin límites del plan gratuito ni pausas) y plan gratuito para la entrega. Row Level Security en todas las tablas, dos roles (`admin`, `member`), registro público apagado.
- **Consecuencias.**
  - Un solo stack para auth, base, realtime y storage. Studio para inspección manual.
  - Aislamiento por usuario fuerte: una fila ajena aparece como 404 en PostgREST.
  - La api reenvía el JWT a PostgREST y RLS hace el filtrado; `service_role` solo se usa en escrituras del backend (engine, agents, workers) y en `POST /admin/users`.
  - Realtime publica `trades`, `equity_snapshots`, `bots` y `strategies` para que el dashboard se actualice solo.
  - Riesgo: el plan gratuito pausa por inactividad. Mitigación: la entrega usa un proyecto con seed y, si hace falta, se migra a un proyecto pagado.

Referencias: [`supabase/README.md`](../../supabase/README.md); propuesta sección 4; PR #54 (esquema inicial).

## 3. Kronos como modelo de pronóstico

- **Contexto.** Se quería una señal basada en ML. Opciones: entrenar un modelo propio, LSTM/Transformer desde cero, modelos pre-entrenados.
- **Decisión.** Usar [Kronos](https://github.com/shiyu-coder/Kronos) (MIT, Shi et al., AAAI 2026) `Kronos-small` (24.7M parámetros, contexto de 512 velas) en zero-shot, solo inferencia. Sleeve propio, decisiones cada 4 horas, ~20 trayectorias por decisión. Servicio FastAPI aparte (`services/forecast`).
- **Consecuencias.**
  - Ganancia documentada vs. baselines (RankIC ~93 % superior según el paper).
  - No entrenamos: calendario realista y ningún look-ahead desde nuestra parte.
  - Contaminación acotada: los datos de pre-entrenamiento llegan a junio de 2024; solo evaluamos a Kronos de julio de 2024 en adelante.
  - Kronos es lento sin caché de KV (~0.6 s/trayectoria en MPS, ~1.1 s en CPU). Mitigación: decisiones cada 4 h, ~20 trayectorias, pronósticos del backtest precalculados en Parquet. El issue [#62](https://github.com/CarloSantoro21/trading_maxxing/issues/62) propone caché de KV.

Referencias: propuesta secciones 2.1 y 2.2; [`services/forecast/README.md`](../../services/forecast/README.md); PRs #61 y #70.

## 4. Local primero, nube diferida hasta go/no-go

- **Contexto.** El proyecto es académico con presupuesto limitado. AWS incluso en Fargate ARM cuesta ~100–120 USD/mes; sin validación del sistema, ese dinero no aporta.
- **Decisión.** Todo corre en localhost durante el desarrollo (Mac M5 con 16 GB RAM). La nube solo se activa para la entrega o si la validación **go/no-go** da `go`, con criterios previos (≥ 5 días de paper trading sin caídas, estrategias aprobadas y P&L neto positivo con drawdown dentro del límite).
- **Consecuencias.**
  - Las issues de despliegue llevan la etiqueta `deploy-diferido`.
  - Terraform está escrito y validado en CI sin aplicarse (ver `infra/README.md`). Se puede desplegar con `terraform apply` en cuanto haya cuenta.
  - Alerta de presupuesto incluida (`infra/modules/budget`, 50 USD).
  - Plan de salida: `terraform destroy` después de la demo.
  - Riesgo: AWS Academy puede no cubrir todos los servicios necesarios. Mitigación: módulos pensados para ARM Fargate, sin NAT Gateway, con ElastiCache mínimo.

Referencias: propuesta sección 5.0; [`infra/README.md`](../../infra/README.md); issue [#44](https://github.com/CarloSantoro21/trading_maxxing/issues/44).

## 5. Capa compartida + capa por usuario (sección 3.4 de la propuesta)

- **Contexto.** El sistema sirve a < 10 usuarios con portafolios distintos. Opciones: calcular todo por usuario, calcular todo global.
- **Decisión.** Partir en dos capas:
  - **Compartida** (una vez para todos): datos de mercado, señales de cada estrategia, vetos del NewsAgent, SimulationGate, ciclo de vida y pesos globales. Publica en `tm:signals`.
  - **Por usuario** (un `BotRunner` por bot en `running`): peso del sleeve, exposición del nivel de riesgo, `RiskAgent` con sus límites. Publica en `tm:decisions`.
- **Consecuencias.**
  - Kronos se llama **una sola vez por símbolo**: el costo no depende de cuántos bots haya.
  - El estado de las estrategias es un dato y se juzga con un portafolio de referencia a tamaño completo (`ReferenceService`), no con el libro de cada usuario.
  - El motor consulta el `TradingSleeveGuard` antes de cada entrada: fail-closed si el registro falla.

Referencias: [`docs/arquitectura/orquestador.md`](../arquitectura/orquestador.md); PRs #76, #85, #88.

## 6. Walk-forward + Monte Carlo como validación obligatoria

- **Contexto.** La mayoría de los bots públicos están sobreajustados. Queremos que **solo entren** estrategias con evidencia estadística, no con backtest de una sola corrida.
- **Decisión.** Una estrategia entra a shadow solo si pasa criterios fijos ([`criterios-de-aprobacion.md`](../estrategias/criterios-de-aprobacion.md)):
  - Walk-forward de 20 tramos (2021 a 2025), con comisiones y slippage.
  - Monte Carlo (block bootstrap, 10 000 simulaciones) con drawdown p95 a 1 año **< 40 %** y Sharpe out-of-sample **> 0.5** y al menos 100 operaciones (o 30 y 3 años OOS para baja frecuencia).
  - Holdout 2026 (enero–octubre) intocable durante el desarrollo: una sola corrida al final.
- **Consecuencias.**
  - El issue [#84](https://github.com/CarloSantoro21/trading_maxxing/issues/84) contaminó el 2025, por eso ahora el holdout es 2026.
  - Rechazamos Intraday Momentum (comisiones se comen la ventaja) y la primera versión de Trend (rebasa el techo de 40 %).
  - Se aprobó Trend con *volatility targeting* (p95 = 35.6 %).
  - Resultados en [`docs/estado-del-proyecto.md`](../estado-del-proyecto.md) y [`docs/estrategias/`](../estrategias/).

## 7. Niveles de riesgo con tabla única (1 a 5)

- **Contexto.** Cada usuario decide un nivel de riesgo (1 conservador a 5 agresivo). La exposición y el drawdown tolerado tienen que ser consistentes en frontend, api, agentes y base de datos.
- **Decisión.** Centralizar los parámetros en `tm_agents.risk.RISK_TABLE` (1: 10 % DD, 2: 15 %, 3: 20 %, 4: 30 %, 5: 40 %). La base deriva `profiles.max_drawdown` con un trigger a partir de `risk_level`; el wizard de onboarding solo pide nivel y capital (no drawdown).
- **Consecuencias.**
  - Una prueba (`test_risk_levels_sync.py`) garantiza que la migración y el wizard coincidan con la tabla.
  - `profiles.max_drawdown` ya no se captura: si viene en el insert, el trigger lo reemplaza.
  - Exposición por sleeve calculada con una **curva de sensibilidad** medida en el laboratorio (PR #83), no el `min(1, drawdown/p95)` simple.

Referencias: PRs #78, #83, #86; [`supabase/README.md`](../../supabase/README.md).

## 8. Export estático del frontend en Cloudflare Pages

- **Contexto.** Hostear Next.js con SSR implica correr Node; el plan gratuito de Cloudflare Pages solo sirve archivos estáticos.
- **Decisión.** Configurar `next.config.ts` con `output: "export"`. Todo lo dinámico (sesión, lecturas y escrituras a Supabase) se hace en el navegador contra la API y Supabase Realtime.
- **Consecuencias.**
  - No hay route handlers, middleware ni server actions.
  - `next/image` corre con `images.unoptimized`.
  - `scripts/check-export.mjs` valida el export en CI.
  - La imagen Docker sirve el export con `nginxinc/nginx-unprivileged` (`apps/web/nginx.conf` imita a Cloudflare Pages, con reescritura de `/login`).
  - Variables `NEXT_PUBLIC_*` se incrustan al build: cada entorno requiere su propio build.

Referencias: PR #77; [`apps/web/README.md`](../../apps/web/README.md).

## 9. Fail-open en noticias, fail-closed en guardias

- **Contexto.** Conviven decisiones de datos “suaves” (noticias) y controles de riesgo “duros” (estado de la estrategia, límites del usuario). El criterio de fallo no puede ser el mismo.
- **Decisión.**
  - `NewsAgent`: si Redis responde con error, **no veta** (fail-open). Perder una señal buena es peor que no vetar ante una fuga del canal de noticias.
  - `TradingSleeveGuard` del motor y `RiskAgent`: si el registro o los límites fallan, **se bloquea la entrada** (fail-closed). Abrir una posición con una estrategia retirada o con un límite desconocido es peor que no abrir.
  - Los **cierres** (ventas `reduce_only` en spot) siempre pasan: aunque la estrategia esté retirada, su posición se puede cerrar.
- **Consecuencias.** Las decisiones se documentan en el orquestador y están cubiertas por `services/engine/tests/test_orchestrator_hardening.py` (PR #89/#91).

## 10. Convenciones de ramas, commits y PRs

- **Decisión.** Git Flow simplificado (ver [`CONTRIBUTING.md`](../../CONTRIBUTING.md)), Conventional Commits, CI obligatorio en PR, `develop` y `main` protegidas. Squash a `develop`, merge commit a `main`, tag `vX.Y.Z` tras cada release.
- **Consecuencias.** `main` recibe solo releases; `develop` siempre está desplegable; los PRs son unidades revisables. Como `develop` no es la rama por defecto, `Closes #N` no cierra issues automáticamente y se hace a mano al fusionar.

## Decisiones aplazadas

- **ExplainerAgent (LLM)**: queda fuera del camino de decisión, depende de Groq. Issue [#37](https://github.com/CarloSantoro21/trading_maxxing/issues/37).
- **CD a AWS**: el workflow está diseñado; aplica al tener cuenta. Issue [#27](https://github.com/CarloSantoro21/trading_maxxing/issues/27).
- **E2E con Playwright**: issue [#28](https://github.com/CarloSantoro21/trading_maxxing/issues/28).
- **Caché de KV para Kronos**: opcional. Issue [#62](https://github.com/CarloSantoro21/trading_maxxing/issues/62).
- **Skill `nueva-estrategia` para Claude Code**: opcional. Issue [#59](https://github.com/CarloSantoro21/trading_maxxing/issues/59).
