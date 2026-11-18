# Guion de la demo (10 minutos)

Storyboard operativo para la demo final. Está pensado para dos personas: una habla y la otra conduce; con una sola se agrega ~1 minuto por transición.

- **Duración objetivo:** 10 min hablados + 2–3 min de preguntas.
- **Tiempo total en pantalla:** ~13 min.
- **Setup previo (antes de empezar).** Hecho en la máquina: `make up-mps` corriendo, `make forecast-mps` en otra terminal, `make web` en una tercera. Login de demo probado, dashboard poblado con el seed. Pestaña de la API docs abierta por si surge una pregunta técnica.

## Antes de empezar (checklist)

- [ ] Docker corriendo, `supabase status` responde con URLs locales.
- [ ] `make ps` muestra `api`, `agents`, `engine`, `forecast` y `redis` en `healthy`.
- [ ] Browser con dos pestañas: `http://localhost:3000` (web, logueado como demo) y `http://localhost:8000/docs` (API).
- [ ] Terminal con zoom 150 % para `docker compose logs -f agents engine` cuando toque mostrar decisiones en vivo.
- [ ] Terminal secundaria lista para ejecutar `make test` si queda tiempo.
- [ ] Red estable: Kronos ya descargó pesos y tiene el modelo cargado en memoria (`GET http://localhost:8001/health` → `model_loaded=true`).
- [ ] **Plan B si cae internet.** Modo backtest: `uv run pytest services/engine/tests/test_orchestrator_backtest.py -v` y narrar los resultados mostrando los Parquet locales.
- [ ] **Plan B si Binance Testnet falla.** La parte en vivo se omite; se muestra el `tm:decisions` histórico y los últimos `trades` en Supabase Studio.

## Storyboard

| Tiempo | Sección | Qué se muestra | Speaker notes |
|---|---|---|---|
| **0:00 – 0:40** | Intro | Diapositiva o `docs/final/README.md` abierto | «trading_maxxing es una plataforma multiestrategia para < 10 usuarios. Combina Kronos y estrategias validadas sobre NautilusTrader en Binance Testnet. No es asesoría financiera.» Mencionar al equipo y el curso. |
| **0:40 – 2:00** | Problema y propuesta | Diagrama de clases (`docs/propuesta/img/classes.png`) o similar | «Un inversionista no puede vigilar el mercado todo el día; la mayoría de los bots públicos están sobreajustados. Nosotros: biblioteca de estrategias con evidencia, validación obligatoria, SimulationGate por operación y RiskAgent determinista.» |
| **2:00 – 3:00** | Arquitectura | Abrir [`docs/final/arquitectura.md`](arquitectura.md), mostrar el diagrama ASCII | «Capa compartida calcula señales una vez para todos; capa por usuario ejecuta según nivel de riesgo. Mensajería por Redis streams, persistencia en Supabase con RLS, frontend estático en Next.js. Los nombres importantes: `tm:signals`, `tm:decisions`, `AgenticStrategy`.» |
| **3:00 – 4:30** | Onboarding del usuario | Web, incognito, login como demo (`demo@trading-maxxing.local` / `demo1234`) | «El usuario solo decide capital, nivel de riesgo (1–5) y metas. Todo lo demás es de la app. El seed ya trae perfil, meta y bot; el wizard real pide esos tres campos y la base deriva el drawdown.» |
| **4:30 – 6:30** | Dashboard y realtime | Pestaña principal, pestaña *Estrategias*, pestaña *Trades* | «Dashboard con equity, P&L y probabilidad de alcanzar la meta (Monte Carlo). En la pestaña de estrategias, `kronos` está en shadow: un admin puede promoverlo. Abro otra pestaña para mostrar los eventos de Realtime cuando llegue un trade nuevo.» |
| **6:30 – 8:00** | Decisión en vivo | Terminal con `docker compose logs -f agents engine`; esperar un cierre de vela | «Al cerrar una vela, la capa compartida publica un `SignalEvent`. El `BotRunner` del demo decide y publica un `DecisionEnvelope`. El motor manda una orden bracket. Si la SimulationGate dice que el valor esperado no es positivo, se bloquea; si el RiskAgent dice BLOCK, también.» Si la vela tarda, mostrar el `XLEN tm:decisions` histórico y abrir un trade con fecha reciente. |
| **8:00 – 9:00** | Validación y laboratorio | Carpeta [`docs/estrategias/reportes/`](../estrategias/reportes/) y [`docs/estrategias/resultados-trend.md`](../estrategias/resultados-trend.md) | «Cada estrategia pasa walk-forward + Monte Carlo antes de shadow. Trend con volatility targeting: Sharpe 0.78, DD p95 a 1 año 35.6 %, aprobada. Intraday Momentum rechazada: las comisiones se comen la ventaja. 2025 quedó contaminado, por eso el holdout real es 2026.» |
| **9:00 – 9:40** | CI/CD y ramas | Abrir [`docs/final/pipeline.md`](pipeline.md) o GitHub Actions | «PRs corren ruff, pytest ≥ 70 %, eslint, tsc, vitest y build de 5 imágenes. `develop` publica a Docker Hub con tag `develop`; un tag `vX.Y.Z` publica `X.Y.Z` y `latest`. Deploy a Cloudflare Pages y a AWS son workflows manuales con aprobación.» |
| **9:40 – 10:00** | Estado y cierre | [`docs/estado-del-proyecto.md`](../estado-del-proyecto.md) con la tabla de pendientes visible | «38 de 53 issues cerrados. Lo que falta depende sobre todo de cuentas externas: Binance Testnet, AWS, Cloudflare. Local primero, nube para la entrega. Gracias.» |

## Qué mostrar al mover el ratón

- **En el dashboard:** señalar la tarjeta de equity, la de drawdown y la de probabilidad de la meta.
- **En estrategias:** destacar la columna de estado (`active`, `probation`, `shadow`) y el botón de promover (solo visible al admin).
- **En los logs:** resaltar las líneas `tm:signals`, `tm:decisions` y `AgenticStrategy.on_signal`.
- **En la API docs:** abrir `POST /strategies/{name}/promote` y `POST /bots/{id}/start` para enseñar los contratos.

## Fallas previstas y mitigaciones

| Falla | Cómo se verá | Mitigación en vivo |
|---|---|---|
| Binance Testnet no responde | El engine logea `AuthError` o `503` | Cambiar a modo offline: mostrar `test_orchestrator_backtest.py` y los Parquet. |
| Kronos lento | El forecast tarda > 3 s en responder | Mencionar la caché de KV como mejora pendiente (issue #62). Mostrar trajectory precomputed. |
| Redis sin mensajes | `XLEN tm:decisions` → 0 | El ciclo del orquestador es por cierre de vela; esperar o mostrar `tm:signals` con mensajes previos. |
| Supabase Realtime no entrega eventos | El dashboard no se actualiza al ejecutar un trade | Refrescar la página o mostrar los eventos directamente en Studio (`http://127.0.0.1:54323`). |
| Un contenedor en `unhealthy` | `make ps` lo marca | `docker compose restart <servicio>`; el seed sigue estando porque Supabase es aparte. |
| JWT expirado | La web pide login de nuevo | Volver a entrar como demo; está siempre disponible. |

## Q&A: preguntas probables y respuestas cortas

- **¿Por qué solo spot y long?** Porque Binance Testnet es spot y queremos reducir superficie: `long` = USDT comprado, `flat` = USDT. Sin cortos. La propuesta lo documenta.
- **¿Cómo validan que no hay look-ahead?** El `AgentContext` arma solo con velas **cerradas**; walk-forward con tramos disjuntos y Monte Carlo sobre los retornos del out-of-sample. Kronos se evalúa solo después de junio de 2024 porque sus datos de preentrenamiento llegan hasta ahí.
- **¿Qué pasa si una estrategia se retira con posición abierta?** El `LifecycleMonitor` escribe el nuevo estado; el BotRunner emite la venta y el motor la ejecuta aunque el sleeve esté retirado. Prueba E2E: `services/engine/tests/test_orchestrator_hardening.py`.
- **¿Cómo aíslan usuarios entre sí?** RLS en todas las tablas de Supabase. Una fila ajena aparece como 404 en PostgREST. La API solo usa `service_role` para `POST /admin/users`.
- **¿Qué se desplegó en AWS?** Nada aún: Terraform está escrito y validado en CI, pero el `apply` requiere cuenta. Parte de la entrega académica (issue [#44](https://github.com/CarloSantoro21/trading_maxxing/issues/44), go/no-go).
- **¿Qué pasa si Groq está caído?** El `NewsAgent` deja de clasificar pero no bloquea nada: fail-open. Las estrategias siguen operando. El `ExplainerAgent` (justificaciones en el dashboard) queda en “pendiente” para ese trade.
- **¿Por qué 70 % de cobertura y no 80 %?** Techo realista para el calendario; la parte crítica (orquestador, riesgo, motor) tiene cobertura mayor. Los módulos de IO quedaron más abajo.
- **¿Qué falta?** Lista en `docs/estado-del-proyecto.md`: Binance Testnet (#11), paper trading continuo (#44), E2E (#28), AWS (#26/#27/#39), Cloudflare (#45), ExplainerAgent (#37).

## Después de la demo

- Dejar `make ps` corriendo si hay tiempo para preguntas técnicas.
- Tener abiertas en el escritorio:
  - [`docs/final/README.md`](README.md) y [`docs/estado-del-proyecto.md`](../estado-del-proyecto.md).
  - [`docs/final/arquitectura.md`](arquitectura.md) y [`docs/arquitectura/orquestador.md`](../arquitectura/orquestador.md).
  - [`docs/final/decisiones.md`](decisiones.md) por si alguien pregunta “¿por qué eligieron X?”.
- `make down` después del evento, o dejar los servicios arriba si va a haber preguntas con detalle.

## Ensayo (pendiente de equipo)

Este guion es el artefacto. El **ensayo en vivo** del equipo queda fuera del alcance de este issue técnico: sugerencia de realizar al menos dos pasadas completas con cronómetro y verificar los *plans B* antes del día de la entrega.
