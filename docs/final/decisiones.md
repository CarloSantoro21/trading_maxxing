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

## 2. ~~Supabase para Postgres, Auth y Realtime~~ (superada por la #12)

> **Superada el 21-nov-2026 por la ADR #12** (todo en Cloudflare: D1, Access y polling en lugar de Postgres, Supabase Auth y Realtime). Se conserva por trazabilidad.

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

## 4. ~~Local primero, nube diferida hasta go/no-go~~ (superada por la #11)

> **Superada el 17-nov-2026 por la ADR #11** (deploy obligatorio), que a su vez quedó superada por la **ADR #12** (Cloudflare + motor en la Mac). Se conserva por trazabilidad.

- **Contexto (antes del 17-nov-2026).** El proyecto es académico con presupuesto limitado. AWS incluso en Fargate ARM cuesta ~100–120 USD/mes; sin validación del sistema, ese dinero no aporta.
- **Decisión (antes).** Todo corre en localhost durante el desarrollo (Mac M5 con 16 GB RAM). La nube solo se activa para la entrega o si la validación **go/no-go** da `go`, con criterios previos (≥ 5 días de paper trading sin caídas, estrategias aprobadas y P&L neto positivo con drawdown dentro del límite).
- **Consecuencias (antes).**
  - Las issues de despliegue llevaban la etiqueta `deploy-diferido`.
  - Terraform estaba escrito y validado en CI sin aplicarse (ver `infra/README.md`). Se podía desplegar con `terraform apply` en cuanto hubiera cuenta de AWS.
  - Alerta de presupuesto incluida (`infra/modules/budget`, 50 USD).
  - Plan de salida: `terraform destroy` después de la demo.
- **Por qué quedó superada.** La rúbrica oficial (ver [`docs/rubrica.md`](../rubrica.md), PR #104) solo acepta **Azure Portal o Cloudflare**, pide **dos ambientes (dev y prod)** en los dos repos y evalúa métricas del **App Service Plan**. El deploy ya no depende del go/no-go: es obligatorio. El Terraform de AWS en `infra/` queda deprecado; la infraestructura se rehace con `azurerm` en el repo [`trading_maxxing-infra`](https://github.com/CarloSantoro21/trading_maxxing-infra) (ADR #11).

Referencias: propuesta sección 5.0; [`infra/README.md`](../../infra/README.md); issue [#44](https://github.com/CarloSantoro21/trading_maxxing/issues/44) (reescrito al nuevo alcance).

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
- **Decisión.** Configurar `next.config.ts` con `output: "export"`. Todo lo dinámico se hace en el navegador contra `/api/*` del mismo dominio (desde la ADR #12; antes, contra la API y Supabase Realtime).
- **Consecuencias.**
  - No hay route handlers, middleware ni server actions.
  - `next/image` corre con `images.unoptimized`.
  - `scripts/check-export.mjs` valida el export en CI.
  - La imagen Docker sirve el export con `nginxinc/nginx-unprivileged` (`apps/web/nginx.conf` imita a Cloudflare Pages, con reescritura de `/login`).
  - Con la ADR #12 la web llama a `/api/*` relativo y Pages lo reenvía al Worker por service binding: el mismo build sirve para dev y prod salvo el indicador de ambiente y SHA.

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

## 11. ~~Migración a Azure App Service + Cloudflare Pages (supersede #4)~~ (superada por la #12)

> **Superada el 21-nov-2026 por la ADR #12.** Nunca se aplicó: se cambió antes de crear recursos en Azure. Se conserva por trazabilidad.

- **Fecha.** 2026-11-17.
- **Contexto.** Llegó la rúbrica oficial del curso (ver [`docs/rubrica.md`](../rubrica.md), PR #104). Solo acepta **Azure Portal o Cloudflare** como nube, pide **dos ambientes (dev y prod)** en el repo del backend y en el del frontend, evalúa **APM** y **métricas del App Service Plan** (CPU, memoria, requests) y exige un **repo separado de Terraform**. El deploy ya no depende del go/no-go: cuenta como entregable.
- **Decisión.**
  - **Backend**: **Azure App Service** (plan B1) con contenedores Linux para `api` y `agents`. Imágenes en Docker Hub. Dos ambientes (dev y prod) cada uno en su resource group, con el mismo plan por ambiente. Observabilidad con **Application Insights** (OpenTelemetry) y **Azure Monitor** (CPU, memoria y requests del App Service Plan).
  - **Frontend**: se mantiene **Cloudflare Pages** (export estático, ADR #8) pero ahora con **dos proyectos**: `trading-maxxing-dev` (branch `develop`) y `trading-maxxing-prod` (branch `main`, con *required reviewer*).
  - **Infraestructura como código**: repo separado [`trading_maxxing-infra`](https://github.com/CarloSantoro21/trading_maxxing-infra) con provider **`azurerm`** y stacks `envs/dev` y `envs/prod`. OIDC federado desde GitHub Actions para `apply`.
  - **Pipelines**: push a `develop` publica a Docker Hub y despliega automáticamente a los dos ambientes dev; push a `main` requiere aprobación manual en los *environments* `prod`.
  - **Secretos**: **Azure Key Vault** por ambiente, consumidos por las Web Apps con `@Microsoft.KeyVault(...)`.
- **Consecuencias.**
  - El Terraform de AWS en `infra/` (PR #79) queda **deprecado**. Se retira cuando el repo de infra tome el relevo.
  - Los pipelines del monorepo cambian: ya no son solo CI, también hacen CD a dev y prod (ver [`pipeline.md`](pipeline.md)).
  - El pipeline del backend suma **UAT con Playwright** (issue [#28](https://github.com/CarloSantoro21/trading_maxxing/issues/28)) además de la cobertura ≥ 70 %.
  - La API expone un **endpoint de demo que lanza un error controlado** para mostrar el flujo APM (issue [#29](https://github.com/CarloSantoro21/trading_maxxing/issues/29)).
  - Riesgo: la cuenta **Azure for Students** (100 USD de crédito) basta para un plan B1 por ambiente durante la demo; después hay que destruir con Terraform para no gastar el crédito.
- **Alternativas descartadas.** Mantener AWS (no cumple la rúbrica); mover todo a Cloudflare Workers (no soporta los contenedores del backend); Azure Container Apps (más caro y con escalado innecesario para la demo).

Referencias: [`docs/rubrica.md`](../rubrica.md); issues [#26](https://github.com/CarloSantoro21/trading_maxxing/issues/26), [#27](https://github.com/CarloSantoro21/trading_maxxing/issues/27), [#29](https://github.com/CarloSantoro21/trading_maxxing/issues/29), [#39](https://github.com/CarloSantoro21/trading_maxxing/issues/39), [#44](https://github.com/CarloSantoro21/trading_maxxing/issues/44), [#45](https://github.com/CarloSantoro21/trading_maxxing/issues/45).

## 12. Todo en Cloudflare (plan gratuito) + motor en la Mac (supersede #2 y #11)

- **Fecha.** 2026-11-21.
- **Contexto.** Azure for Students tiene crédito limitado y el App Service Plan B1 por ambiente, más Redis y Key Vault, se lo come en semanas; además había que sostener Supabase aparte. La rúbrica acepta **Cloudflare** como nube, y Cloudflare cubre frontend, API, base, login, APM y métricas en el **plan gratuito**.
- **Decisión.**
  - **Frontend**: Cloudflare **Pages**, proyectos `tm-web-dev` (rama de producción `develop`) y `tm-web` (`main`).
  - **API**: Cloudflare **Workers** con **Hono + TypeScript** (`apps/api`, PR #120), Workers `tm-api-dev` y `tm-api`. Reemplaza a `services/api` (FastAPI) y a PostgREST. Contrato en [`docs/arquitectura/api.md`](../arquitectura/api.md).
  - **Base**: **D1** (`tm-dev`, `tm-prod`), port de las migraciones de Supabase a SQLite; RLS pasa a filtros por usuario en la API.
  - **Login**: Cloudflare **Access** (allowlist + One-time PIN) delante de Pages. Pages reenvía `/api/*` al Worker por el **service binding `API`**: un solo origen, la cookie de Access funciona sin CORS.
  - **APM y métricas**: **Workers Observability** (logs, trazas, errores) y el panel del Worker (requests, CPU time).
  - **Motor**: agents, engine (NautilusTrader), forecast (Kronos) y Redis en la **Mac de Carlo** con `docker compose`, imágenes de Docker Hub y **Watchtower**. Solo conexiones salientes a Binance Spot Testnet y a `/internal/*` (service token de Access + `X-Service-Token`).
  - **IaC**: Terraform `cloudflare/cloudflare` en [`trading_maxxing-infra`](https://github.com/CarloSantoro21/trading_maxxing-infra) (D1, Pages, Access).
- **Consecuencias.**
  - Costo en la nube: **0 USD**. No hay que destruir nada después de la demo.
  - El motor no puede ir en Workers: Python corre en Pyodide sin paquetes nativos (NautilusTrader, PyTorch), no hay procesos permanentes, 128 MB de memoria y 10 ms de CPU por invocación en el plan gratuito. Ver [`cloudflare.md` §4](../arquitectura/cloudflare.md).
  - Workers no reporta memoria: el criterio de métricas se cubre con requests y CPU time del Worker más `docker stats` de la Mac (riesgo a confirmar con el profesor).
  - Realtime desaparece: el dashboard hace *polling*. Los comandos `start` / `stop` / `kill` y los backtests en cola pasan de Redis a tablas de D1 que el motor lee con *polling*.
  - Si la Mac se apaga, el motor se detiene; la web lo muestra como "motor desconectado" por el latido.
  - **Cuando haya presupuesto**: Cloudflare Containers (Workers Paid, 5 USD al mes) con las mismas imágenes, un Durable Object supervisor, Redis en contenedor o Queues, service binding interno y métricas de CPU y memoria por contenedor; cuidando la región por el bloqueo de Binance a IPs de EE. UU. Ver [`cloudflare.md` §5](../arquitectura/cloudflare.md).
- **Alternativas descartadas.** Seguir con Azure (crédito insuficiente, Supabase aparte); Cloudflare Containers desde ya (requiere plan de pago); túnel de Cloudflare hacia la Mac para exponer la API (innecesario: el motor solo sale).

Referencias: [`docs/arquitectura/cloudflare.md`](../arquitectura/cloudflare.md); [`arquitectura.md §Despliegue`](arquitectura.md#despliegue-cloudflare--motor-en-la-mac); [`pipeline.md`](pipeline.md); issues #115, #116, #117, #26, #27, #29, #45.

## 13. LLM y noticias opcionales, apagados por defecto

- **Fecha.** 2026-11-20 (issue #118, PR #119).
- **Contexto.** Depender de Groq o de otro proveedor de LLM agrega costo, llaves y un punto de falla que no aporta a la rúbrica ni a la decisión de trading.
- **Decisión.** `NewsAgent` y `ExplainerAgent` quedan apagados por defecto (`NEWS_ENABLED=false`, `EXPLAINER_ENABLED=false`, `LLM_PROVIDER=none`). Si se prenden, las noticias vienen de RSS gratis (o CryptoPanic) y el LLM puede ser Workers AI, Groq o un endpoint compatible con OpenAI. Un admin los prende en caliente con `PUT /api/admin/settings`.
- **Consecuencias.** Ninguna estrategia, ni el riesgo, ni la SimulationGate dependen del LLM; el sistema corre completo sin llaves de terceros salvo Binance Testnet.

## Decisiones aplazadas

- **ExplainerAgent (LLM)**: opcional y apagado por defecto (ADR #13). Issue [#37](https://github.com/CarloSantoro21/trading_maxxing/issues/37).
- **Motor en Cloudflare Containers**: cuando haya plan de pago (ADR #12).
- **Caché de KV para Kronos**: opcional. Issue [#62](https://github.com/CarloSantoro21/trading_maxxing/issues/62).
- **Skill `nueva-estrategia` para Claude Code**: opcional. Issue [#59](https://github.com/CarloSantoro21/trading_maxxing/issues/59).
