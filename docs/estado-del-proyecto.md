# Estado del proyecto (21 de noviembre de 2026)

Documento de traspaso para el equipo. Manda la **[rúbrica oficial](rubrica.md)**: entrega y demo
el **6 de diciembre de 2026**.

## Resumen

**47 de 60 issues cerrados.** Están completos el motor, los datos, el laboratorio, los agentes,
el orquestador, el frontend principal, el CI de pruebas e imágenes y la API nueva en Cloudflare
Workers. Lo que falta es, sobre todo, **la migración a Cloudflare y el deploy** (ruta crítica de
la rúbrica).

Lo nuevo desde el 8 de noviembre:

- **PRs de Javi fusionados** (#96 a #107): `MovingAverageAgent` SMA 50/100 (#82), `TsmomAgent`
  semanal (#81), skill `nueva-estrategia` (#59), scaffold del go/no-go (#44), Wiki en
  `docs/wiki/` con script de publicación (#7), `ExplainerAgent` con plantilla de respaldo
  (#37), documentación final (#30), `TradingNode` contra Binance Testnet con smoke test (#11),
  E2E con Playwright (#28) y caché de KV de Kronos (#62). Release a `main` en el PR #114.
- **Auditor multimodal (#108) congelado** hasta después de la entrega: los PRs #110 a #113 se
  cerraron sin fusionar.
- **LLM y noticias opcionales** (#118, PR #119): apagados por defecto
  (`NEWS_ENABLED=false`, `EXPLAINER_ENABLED=false`, `LLM_PROVIDER=none`), con proveedores
  gratis si se prenden (RSS, Workers AI, Groq).
- **API en Cloudflare Workers** (#115, PR #120): Hono + TypeScript + D1, login con Access,
  endpoints `/api/*` para la web y `/internal/*` para el motor. Contrato en
  [`arquitectura/api.md`](arquitectura/api.md).
- **Migración a Cloudflare en curso** (decisión del 21 de noviembre, ver abajo).

## Arquitectura final

Todo lo público en **Cloudflare, plan gratuito**, sin Supabase:

- **Pages** (`tm-web-dev` / `tm-web`): export estático de Next.js; reenvía `/api/*` al Worker
  por el service binding `API`.
- **Workers + Hono** (`tm-api-dev` / `tm-api`) con **D1** (`tm-dev` / `tm-prod`).
- **Access** (Zero Trust) delante de Pages, y una app con *service token* para el motor.
- **Workers Observability** para APM y métricas.
- **Motor en la Mac de Carlo**: agents, engine (NautilusTrader), forecast (Kronos) y Redis con
  `docker compose`, imágenes de Docker Hub y Watchtower. Solo conexiones salientes a Binance
  Spot Testnet y a `/internal/*` del Worker.
- **Terraform** de Cloudflare en el repo separado
  [`trading_maxxing-infra`](https://github.com/CarloSantoro21/trading_maxxing-infra).

Por qué el motor no va en Workers y el plan con Cloudflare Containers:
[`arquitectura/cloudflare.md`](arquitectura/cloudflare.md). Diagramas en la
[Wiki](wiki/Infraestructura.md).

## Para entender el diseño, en este orden

1. [Propuesta](propuesta/Propuesta-trading_maxxing.md): secciones 3.2 (flujo), 3.3 (ciclo de
   vida) y 3.4 (qué es de la app y qué es del usuario).
2. [Arquitectura final en Cloudflare](arquitectura/cloudflare.md) y
   [contrato de la API](arquitectura/api.md).
3. [Arquitectura del orquestador](arquitectura/orquestador.md).
4. [Criterios de aprobación](estrategias/criterios-de-aprobacion.md) y resultados del
   [Trend](estrategias/resultados-trend.md) y del
   [Momentum](estrategias/resultados-intraday-momentum.md).

## Cómo levantarlo en local

Mientras terminan #116 y #117, el motor y la web todavía leen de Supabase local; la API nueva ya
corre sola con D1 local. Ver [`final/setup-local.md`](final/setup-local.md).

```bash
cd apps/api && pnpm i && cp .dev.vars.example .dev.vars
pnpm db:migrate:local && pnpm db:seed:local && pnpm dev    # API en http://localhost:8787
make up && make web                                        # motor y web (aún con Supabase local)
make test && make lint
```

- **Datos históricos:** no están en git. Se bajan con
  `uv run --package tm-marketdata python -m tm_marketdata download --symbols BTCUSDT ETHUSDT --tf 1m 15m 30m 1d --start 2020-01-01 --end 2026-10-31`.
- **Pronósticos de Kronos:** `services/forecast/scripts/precompute.py` (más rápido con la caché
  de KV de #62).

## Decisiones tomadas

| Tema | Decisión |
|---|---|
| Mercado | Solo cripto: BTC/USDT y ETH/USDT en Binance **Spot Testnet**. `long` = comprar, `flat` = USDT; no se abren cortos |
| Arquitectura del motor | **Capa compartida** (señales, laboratorio, noticias, SimulationGate y ciclo de vida, una vez) y **capa por usuario** (cada bot según su capital y su nivel de riesgo del 1 al 5) |
| Usuarios | Menos de 10 personas. Login con Cloudflare Access (allowlist); roles admin y member en D1; cada quien ve solo lo suyo (filtros por usuario en la API) |
| Estrategias | Entran por PR, se validan en el laboratorio (walk-forward + Monte Carlo) y pasan por shadow ≥ 7 días antes de que un admin las promueva. Degradación automática por el p95 y el p99 |
| Validación | Walk-forward de 20 tramos, de 2021 a 2025. **Holdout 2026 (enero a octubre): no se ha corrido** |
| Nube | **Cloudflare, plan gratuito** (Pages, Workers, D1, Access, Observability); el motor en la Mac. El deploy es obligatorio y ya no depende del go/no-go (#44) |
| LLM | Opcional y apagado por defecto; ninguna decisión de trading depende de él |

## Resultados del laboratorio

Fuera de muestra 2021–2025, con comisiones:

| Estrategia | Sharpe | DD máximo | DD p95 a 1 año | Veredicto |
|---|---|---|---|---|
| Trend | 0.93 | 33.9 % | 43.8 % | Rechazada: rebasa el techo de 40 % |
| Trend con volatility targeting | 0.78 | 24.6 % | 35.6 % | **Aprobada** |
| Trend al 77 % fijo (referencia) | 0.93 | 26.9 % | 35.4 % | — |
| Momentum intradía | −2.65 | — | 15.1 % | Rechazada: las comisiones se comen la ventaja |
| Comprar y mantener | 0.71 | 77.4 % | — | — |

**Decisión pendiente:** aprobar el Trend con tope fijo o con volatility targeting, o correr el
holdout 2026 una sola vez con ambos. Los laboratorios de Kronos, SMA 50/100 y TSMOM están
pendientes de correr.

## Pendientes (ruta crítica de la rúbrica primero)

| Issue | Qué falta | Criterio de la rúbrica |
|---|---|---|
| #26, #39, #109 | Terraform de Cloudflare: `apply` de D1, Pages y Access en dev y prod, estado remoto y CI del repo de infra | Terraform, Ambientes |
| #117 | Frontend sin Supabase: login con Access, datos por `/api/*` y polling | Ambientes, Pipeline del front |
| #116 | Motor en la Mac: `WorkerApiBackend` con `TM_API_URL` / `TM_SERVICE_TOKEN`, runner con Docker Hub + Watchtower y heartbeat | (necesario para ver trades reales) |
| #27 | Pipeline del backend: cobertura visible, deploy del Worker y D1 a dev y prod con aprobación, imágenes del motor | Pipeline del backend, Ambientes, Aprobación, Cambio en Prod |
| #45 | Pipeline del frontend: Pages en dev y prod con aprobación e indicador de versión | Pipeline del front, Cambio en Dev y en Prod |
| #28 | UAT contra dev en el pipeline | Pipeline del backend |
| #29 | Workers Observability: trazas, error de demo y métricas (preguntar si basta CPU + requests) | APM, Métricas |
| #103 | PBI → Story → Task en el board y protección de ramas | Plan de trabajo, Ramas |
| #7 | Publicar la Wiki con `scripts/publish_wiki.sh` | Wiki |
| #30 | Ensayo de la demo con el guion de [`final/demo-guion.md`](final/demo-guion.md) | Presentación |
| #44 | Go/no-go en paper trading (ya no condiciona el deploy) | — |

Puntos menores: gráfica de velas con el pronóstico de Kronos en el dashboard, enlaces del
dashboard a `/backtests` y `/goals`, y que los SL/TP emulados sobrevivan a un reinicio del
motor. Al terminar #115–#117 se borran `services/api`, `supabase/` y el Terraform de AWS en
`infra/`.

## Cómo trabajar en el repo

- **Ramas:** `feature/<issue>-<descripcion>` desde `develop`, PR a `develop` con **squash
  merge**. `main` solo recibe `develop` o `release/*` con **merge commit**.
- **Commits:** Conventional Commits (`feat:`, `fix:`, `test:`, `ci:`, `docs:`).
- **CI obligatorio:** ruff, pytest con cobertura ≥ 70 %, eslint, tsc, vitest (web y
  `apps/api`) y build de las imágenes.
- **Issues:** `develop` no es la rama por defecto, así que `Closes #N` no los cierra solo.
  Ciérralos a mano al fusionar.
- **Lockfiles:** si `uv.lock` tiene conflicto, toma una versión y corre `uv lock`. Lo mismo con
  `pnpm-lock.yaml` y `pnpm install`.
- **Llaves:** solo en `.env` / `.dev.vars` (en `.gitignore`) o en los secrets de GitHub y de
  `wrangler`. Nunca van al repo.
