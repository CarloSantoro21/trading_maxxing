# Guion de la demo (10–12 minutos)

Storyboard operativo para la demo final. Está pensado para dos personas: una habla y la otra conduce; con una sola se agrega ~1 minuto por transición.

- **Duración objetivo:** 10–12 min hablados + 2–3 min de preguntas.
- **Orden fijo.** Los 10 pasos siguen el orden de la **sección 2 de la [rúbrica oficial](../rubrica.md)** (PR #104) y el issue [#30](https://github.com/CarloSantoro21/trading_maxxing/issues/30). Si algo se cae en vivo, se usa el **Plan B** (video grabado) sin romper el orden.

## Antes de empezar (checklist)

- [ ] Sesión iniciada en **GitHub** (como *maintainer* del repo) y en el **Azure Portal** con la suscripción de Azure for Students.
- [ ] Cuenta demo del frontend lista: `demo@trading-maxxing.local` / `demo1234`.
- [ ] Pestañas abiertas en el navegador, **en el mismo orden del guion**:
  1. GitHub, [`docs/final/README.md`](README.md) del monorepo.
  2. GitHub, [`CONTRIBUTING.md`](../../CONTRIBUTING.md) + diagrama [`docs/propuesta/img/branches.png`](../propuesta/img/branches.png).
  3. GitHub, vista de **PRs** con uno recién abierto (ver Paso 3).
  4. GitHub Actions, el run del pipeline del PR.
  5. **URL de dev** del frontend en Cloudflare Pages (`https://dev.trading-maxxing.pages.dev` o similar).
  6. GitHub Actions, el run con la aprobación pendiente del *environment* `prod`.
  7. **URL pública de prod** del frontend (`https://trading-maxxing.pages.dev` o custom domain).
  8. **Application Insights** en el portal de Azure, con el filtro de la última hora.
  9. **Azure Monitor** → *App Service Plan* del backend, métricas de CPU, memoria y requests.
  10. GitHub, el repo [`trading_maxxing-infra`](https://github.com/CarloSantoro21/trading_maxxing-infra).
- [ ] Red estable; VPN apagado si Azure Portal pide 2FA.
- [ ] **Plan B general:** video grabado de 10 min con los 10 pasos en orden, disponible offline. Se usa si cae internet, si Azure se demora o si el pipeline falla por una razón ajena al código.
- [ ] **Plan B parcial:** si solo cae el deploy a dev, se muestra el run anterior exitoso y se narra el pipeline sobre él.

## Storyboard (10 pasos del orden de evaluación)

| # | Tiempo | Paso (rúbrica §2) | Qué se muestra | Speaker notes |
|---|---|---|---|---|
| **1** | **0:00 – 1:00** | **Descripción del proyecto y tecnología** | [`docs/final/README.md`](README.md) + diagrama de clases ([`docs/propuesta/img/classes.png`](../propuesta/img/classes.png)) | «trading_maxxing es una plataforma multiestrategia para < 10 usuarios. Combina Kronos y estrategias validadas sobre NautilusTrader en Binance Testnet. Stack: Next.js 15, FastAPI, Supabase, Redis, Docker; infra en **Azure App Service** y **Cloudflare Pages**, Terraform con `azurerm` en un repo aparte. No es asesoría financiera.» |
| **2** | **1:00 – 1:45** | **Estrategia de ramas** | [`CONTRIBUTING.md`](../../CONTRIBUTING.md) + diagrama [`branches.png`](../propuesta/img/branches.png) | «Git Flow simplificado: `feature/*` → `develop` → `main`. PRs con squash a `develop`, merge commit a `main`, tag `vX.Y.Z` en cada release. `develop` y `main` protegidas; PR requiere CI en verde y 1 aprobador.» |
| **3** | **1:45 – 3:00** | **Cambio en el código visible en la UI** | VS Code con un cambio preparado (por ejemplo, texto del hero del dashboard) en una rama `feature/demo-<fecha>` | Abrir el archivo (`apps/web/src/app/page.tsx` o similar), mostrar el diff, commitear y empujar: `git push -u origin feature/demo-<fecha>`. «Hacemos un cambio pequeño en el hero del dashboard para que el jurado pueda identificarlo en Dev y en Prod.» |
| **4** | **3:00 – 4:00** | **Abrir el PR, aprobarlo y hacer el merge a `develop`** | GitHub, pestaña *Pull requests* | `gh pr create --base develop --title "demo: cambio visible en la UI"`. Mostrar los checks del CI corriendo en el PR. Esperar al verde (o enseñar uno pre-aprobado si tarda). Aprobar con un segundo usuario y hacer *squash merge* a `develop`. |
| **5** | **4:00 – 6:00** | **Pipeline de dev: build, pruebas unitarias, cobertura, deploy, UAT** | GitHub Actions, run del push a `develop` | Abrir el run; mostrar en orden: **(a)** job de CI con `ruff`, **(b)** `pytest --cov` con el porcentaje de cobertura ≥ 70 %, **(c)** build multi-arch y push a Docker Hub, **(d)** job `deploy-dev` que llama a `az webapp config container set` contra Azure App Service dev, **(e)** job `uat` con Playwright contra la URL de dev. «Cada push a `develop` corre pruebas unitarias con cobertura, publica a Docker Hub, despliega a Azure dev y ejecuta la UAT. La cobertura y la UAT cuentan para los 10 pts del pipeline del backend.» |
| **6** | **6:00 – 6:40** | **Abrir la URL de dev y ver el cambio** | Pestaña del navegador con la URL de dev | Refrescar; el cambio del Paso 3 es visible. «Este es el ambiente dev en Cloudflare Pages, con `NEXT_PUBLIC_API_URL` apuntando al API en Azure App Service dev.» |
| **7** | **6:40 – 8:10** | **PR `develop` → `main`, aprobar environment prod, ver el cambio en la URL pública** | GitHub: PR `develop` → `main`; GitHub Actions: aprobación del environment `prod`; navegador: URL pública | `gh pr create --base main --head develop --title "release: demo"`. Mergear con *merge commit*. Al push a `main`, el pipeline de prod publica `vX.Y.Z` a Docker Hub y **queda esperando aprobación** en el environment `prod` (*required reviewer*). Aprobar en vivo; ver el deploy terminar. Refrescar la URL pública de prod; el cambio aparece. |
| **8** | **8:10 – 9:30** | **APM en Application Insights: requests, trazas, error y logs** | Azure Portal → recurso de Application Insights del ambiente **prod** | Mostrar: **(a)** la gráfica de *Requests* de la última hora, **(b)** el panel *Live Metrics* con requests por segundo, **(c)** *Transaction search* con una traza del endpoint `GET /strategies`, **(d)** disparar el endpoint de demo que lanza un error controlado (`curl https://<api-prod>/__demo/error`, issue [#29](https://github.com/CarloSantoro21/trading_maxxing/issues/29)), **(e)** verlo aparecer en *Failures* con stack trace y mensaje de log. «Requests, trazas y al menos un error con mensaje: los tres niveles que pide la rúbrica.» |
| **9** | **9:30 – 10:30** | **Métricas en el portal de Azure: CPU, memoria y requests del App Service Plan** | Azure Portal → App Service Plan de **prod** → *Metrics* | Abrir tres gráficas lado a lado: **CPU Percentage**, **Memory Percentage** y **Requests** (esta última también desde la Web App). Ventana de 30 min. «CPU y memoria del App Service Plan, requests del backend: los tres indicadores que evalúan en el portal (5 pts).» |
| **10** | **10:30 – 11:30** | **Terraform: repo `trading_maxxing-infra`** | GitHub, repo [`trading_maxxing-infra`](https://github.com/CarloSantoro21/trading_maxxing-infra) | Mostrar la estructura: `envs/dev/`, `envs/prod/`, `modules/{app-service-plan, web-app, app-insights, key-vault, redis}`. Abrir un `main.tf` corto (por ejemplo el de `app-service-plan`). Enseñar el workflow de Terraform con OIDC a Azure. «Provider `azurerm`, dos stacks, módulos reutilizables, backend remoto en Azure Storage.» |
| cierre | **11:30 – 12:00** | Cierre | [`docs/estado-del-proyecto.md`](../estado-del-proyecto.md) | «Estado al 17 de noviembre: 38 de 53 issues cerrados, ruta crítica terminada hasta la demo. Qué queda: observabilidad fina (dashboards personalizados) y el ensayo final. Gracias.» |

## Qué mostrar al mover el ratón

- **Paso 3 (código):** señalar el archivo y la línea exacta que se cambia; el commit debe ser corto y en Conventional Commits.
- **Paso 5 (pipeline):** enfocar el *summary* del run para que se vean los jobs en paralelo; abrir el job de `pytest` y resaltar la línea de cobertura.
- **Paso 6 (dev):** abrir DevTools → *Network* y mostrar que `fetch` va al API en `*.azurewebsites.net` del ambiente dev.
- **Paso 7 (prod):** destacar el banner de "Environment `prod` waiting for review" y luego el deploy verde.
- **Paso 8 (APM):** tras disparar el error, abrir la traza completa y leer el `exception.type` y el `exception.message`.
- **Paso 9 (métricas):** mencionar que el plan es **B1** y por eso la CPU sube visiblemente al disparar unas pocas requests.
- **Paso 10 (Terraform):** si hay tiempo, mostrar el `plan` cacheado en un PR reciente del repo de infra.

## Fallas previstas y mitigaciones

| Falla | Cómo se verá | Mitigación en vivo |
|---|---|---|
| Caja de GitHub Actions saturada | El run no inicia | Mostrar el **run anterior** verde del mismo workflow; narrar sobre él. |
| Deploy a Azure App Service tarda > 2 min | El Paso 6 o 7 bloquea el guion | Saltar al Paso 8 y 9 (usan datos de hace unos minutos) y volver al 6/7 cuando termine. |
| Aprobación del environment `prod` no refresca | Botón "Review deployments" no aparece | Refrescar la página, o pedir al segundo revisor que apruebe desde su cuenta. |
| Application Insights no muestra el error nuevo | Hay delay de ingesta (~1–3 min) | Disparar el error 2 min antes del Paso 8 (durante el Paso 7). Mostrar un error ingerido previamente mientras llega el reciente. |
| CPU o memoria del App Service Plan planas | No hay tráfico en prod | Lanzar un pequeño *load* con `hey` o `ab` (10 req/s por 20 s) **antes** del Paso 9. |
| Cloudflare Pages no actualiza | El cambio no se ve en prod | Refrescar con *hard reload*; si persiste, abrir el deploy en Cloudflare Pages dashboard y tomar la URL `*.<commit>.trading-maxxing.pages.dev`. |
| **Cae internet o Azure** | Nada carga | **Plan B: video grabado** completo con los 10 pasos en orden (grabar con OBS la víspera, dejarlo en el escritorio). |

## Q&A: preguntas probables y respuestas cortas

- **¿Por qué solo spot y long?** Porque Binance Testnet es spot y queremos reducir superficie: `long` = USDT comprado, `flat` = USDT. Sin cortos. La propuesta lo documenta.
- **¿Cómo validan que no hay look-ahead?** El `AgentContext` arma solo con velas **cerradas**; walk-forward con tramos disjuntos y Monte Carlo sobre los retornos del out-of-sample. Kronos se evalúa solo después de junio de 2024 porque sus datos de preentrenamiento llegan hasta ahí.
- **¿Qué pasa si una estrategia se retira con posición abierta?** El `LifecycleMonitor` escribe el nuevo estado; el BotRunner emite la venta y el motor la ejecuta aunque el sleeve esté retirado. Prueba: `services/engine/tests/test_orchestrator_hardening.py`.
- **¿Cómo aíslan usuarios entre sí?** RLS en todas las tablas de Supabase. Una fila ajena aparece como 404 en PostgREST. La API solo usa `service_role` para `POST /admin/users`.
- **¿Por qué Azure y no AWS?** Antes del 17-nov elegimos AWS; la rúbrica oficial (publicada ese día) solo acepta Azure o Cloudflare y evalúa el App Service Plan. Se migró en el ADR #11 ([`decisiones.md`](decisiones.md)).
- **¿Por qué Cloudflare Pages si usan Azure para el backend?** El frontend es export estático (ADR #8) y Cloudflare Pages ya está integrado al workflow; separar front/back entra en los criterios "dos repos con dos ambientes" de la rúbrica.
- **¿Qué pasa si Groq está caído?** El `NewsAgent` deja de clasificar pero no bloquea nada: fail-open. Las estrategias siguen operando. El `ExplainerAgent` queda en "pendiente" para ese trade.
- **¿Por qué 70 % de cobertura y no 80 %?** Techo realista para el calendario; la parte crítica (orquestador, riesgo, motor) tiene cobertura mayor. Los módulos de IO quedaron más abajo.
- **¿Qué falta?** Lista en [`docs/estado-del-proyecto.md`](../estado-del-proyecto.md): Binance Testnet (#11), E2E (#28), ExplainerAgent (#37), dashboards finos de Azure Monitor.

## Después de la demo

- Dejar las pestañas 8 y 9 (Application Insights y Metrics) abiertas por si hay preguntas de observabilidad.
- Tener a mano:
  - [`docs/final/README.md`](README.md) y [`docs/estado-del-proyecto.md`](../estado-del-proyecto.md).
  - [`docs/final/arquitectura.md`](arquitectura.md) y [`docs/arquitectura/orquestador.md`](../arquitectura/orquestador.md).
  - [`docs/final/decisiones.md`](decisiones.md), especialmente la ADR #11.
- Correr `terraform destroy` desde `trading_maxxing-infra` después del evento si ya no hay preguntas que requieran los recursos vivos (para no gastar el crédito de Azure for Students).

## Ensayo (pendiente de equipo)

Este guion es el artefacto. El **ensayo en vivo** del equipo queda fuera del alcance de este issue técnico: sugerencia de realizar al menos **dos pasadas completas con cronómetro** y verificar que el video del Plan B esté grabado y accesible offline antes del día de la entrega.
