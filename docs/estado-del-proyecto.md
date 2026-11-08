# Estado del proyecto (8 de noviembre de 2026)

Documento de traspaso para el equipo. Lo que está aquí ya está en `develop` y en `main`.

## Resumen

**38 de 53 issues cerrados.** Están completos el motor, los datos, el laboratorio, los agentes, la API, la base de datos, el frontend principal y el CI/CD de pruebas e imágenes. Lo que falta está en la tabla de pendientes y depende sobre todo de cuentas externas: Binance Testnet, AWS, Cloudflare y Docker Hub.

Para entender el diseño, lee en este orden:
1. [Propuesta](propuesta/Propuesta-trading_maxxing.md). Las secciones clave son la 3.2 (flujo), la 3.3 (ciclo de vida de las estrategias) y la 3.4 (qué es de la app y qué es del usuario).
2. [Criterios de aprobación](estrategias/criterios-de-aprobacion.md).
3. [Arquitectura del orquestador](arquitectura/orquestador.md).
4. [Resultados del Trend](estrategias/resultados-trend.md) y [del Momentum](estrategias/resultados-intraday-momentum.md).
5. [Investigación de estrategias nuevas](estrategias/investigacion-nuevas-estrategias.md).

## Cómo levantarlo en local

```bash
cp .env.example .env              # llena las llaves que tengas
make up                           # Supabase (CLI) + Redis + api + agents + engine + forecast
make web                          # frontend en http://localhost:3000
make test && make lint
```

- **Usuario demo (admin):** `demo@trading-maxxing.local` / `demo1234`. Para dar de alta a alguien más: `make user EMAIL=... ROLE=member`.
- **Datos históricos:** no están en git. Se bajan con `uv run --package tm-marketdata python -m tm_marketdata download --symbols BTCUSDT ETHUSDT --tf 1m 15m 30m 1d --start 2020-01-01 --end 2026-10-31`.
- **Pronósticos de Kronos:** `services/forecast/scripts/precompute.py`. Tarda unas 3 h en una Mac M5 con MPS.

## Decisiones tomadas

| Tema | Decisión |
|---|---|
| Mercado | Solo cripto: BTC/USDT y ETH/USDT en Binance **Spot Testnet**. `long` = comprar, `flat` = USDT; no se abren cortos |
| Arquitectura | **Capa compartida** (señales, laboratorio, noticias, SimulationGate y ciclo de vida se calculan una vez) y **capa por usuario** (cada bot ejecuta según su capital y su nivel de riesgo del 1 al 5) |
| Usuarios | App para menos de 10 personas. Cada quien tiene su portafolio, aislado por RLS. Sin registro público; roles admin y member |
| Estrategias | Entran por PR, se validan en el laboratorio (walk-forward + Monte Carlo) y pasan por shadow ≥ 7 días antes de que un admin las promueva. La degradación es automática por el p95 y el p99 |
| Validación | Walk-forward de 20 tramos, de 2021 a 2025. **Holdout 2026 (enero a octubre): no se ha corrido.** El de 2025 quedó contaminado en #84 |
| Despliegue | **Local primero**: la nube solo para la entrega o si el go/no-go (#44) da *go*. El frontend se publica estático en Cloudflare Pages; el backend va en ECS Fargate ARM con Terraform |

## Resultados del laboratorio

Fuera de muestra 2021–2025, con comisiones:

| Estrategia | Sharpe | DD máximo | DD p95 a 1 año | Veredicto |
|---|---|---|---|---|
| Trend | 0.93 | 33.9 % | 43.8 % | Rechazada: rebasa el techo de 40 % |
| Trend con volatility targeting | 0.78 | 24.6 % | 35.6 % | **Aprobada** |
| Trend al 77 % fijo (referencia) | 0.93 | 26.9 % | 35.4 % | — |
| Momentum intradía | −2.65 | — | 15.1 % | Rechazada: las comisiones se comen la ventaja |
| Comprar y mantener | 0.71 | 77.4 % | — | — |

**Decisión pendiente:** aprobar el Trend con un tope fijo de ~75 % o con volatility targeting, o correr el holdout 2026 una sola vez con ambos y decidir con ese resultado. Kronos tiene listos los pronósticos de julio de 2024 a octubre de 2026, pero su laboratorio no se ha corrido.

## Pendientes

| Issue | Qué falta | Bloqueado por |
|---|---|---|
| #11 | Probar el TradingNode contra Binance Testnet; el código está listo | Llaves de Binance Testnet |
| #44 | Go/no-go: ≥ 5 días de paper trading continuo | Llaves de Binance. **Ruta crítica**: debe empezar alrededor del 25 de noviembre |
| #28 | Pruebas E2E con Playwright (login → onboarding → bot → trade → dashboard) | — |
| #81, #82 | Nuevas estrategias: TSMOM semanal y SMA 50/100. Entran en *research* | — |
| #37 | ExplainerAgent (LLM que redacta la justificación) | Llave de Groq |
| #26, #39, #29 | Terraform: el código está, falta el *apply* | Cuenta de AWS (¿AWS Academy?) |
| #27 | CD a AWS (workflow manual) | Cuenta de AWS |
| #45 | Deploy en Cloudflare Pages: el código y el workflow están | Cuenta de Cloudflare y token |
| #30 | Documentación final y demo | — |
| #7 | Wiki | — |
| #59, #62 | Skill de estrategias y caché de KV de Kronos (opcionales) | — |

También quedan pendientes estos puntos menores:
- La gráfica de velas con el pronóstico de Kronos superpuesto en el dashboard.
- Los enlaces desde el dashboard hacia `/backtests` y `/goals`.
- Publicar en CloudWatch las métricas de drawdown y kill switch del motor.
- Los SL/TP emulados no sobreviven a un reinicio del motor.

## Cómo trabajar en el repo

- **Ramas:** `feature/<issue>-<descripcion>` desde `develop`, PR a `develop` con **squash merge**. `main` solo recibe `develop` o `release/*` con **merge commit**.
- **Commits:** Conventional Commits (`feat:`, `fix:`, `test:`, `ci:`, `docs:`).
- **CI obligatorio:** ruff, pytest con cobertura ≥ 70 %, eslint, tsc, vitest y build de las 5 imágenes. Los cambios en `supabase/` corren pgTAP y los de `infra/` corren Terraform validate.
- **Issues:** develop no es la rama por defecto, así que `Closes #N` no los cierra solo. Ciérralos a mano al fusionar.
- **Lockfiles:** si `uv.lock` tiene conflicto, no lo resuelvas a mano. Toma una versión y corre `uv lock`. Lo mismo con `pnpm-lock.yaml` y `pnpm install`.
- **Llaves:** solo en `.env`, que está en `.gitignore`. Nunca van al repo.
