# Guion de la demo (10–12 minutos)

Storyboard operativo para la demo final. Está pensado para dos personas: una habla y la otra conduce; con una sola se agrega ~1 minuto por transición.

- **Duración objetivo:** 10–12 min hablados + 2–3 min de preguntas.
- **Orden fijo.** Los 10 pasos siguen el orden de la **sección 2 de la [rúbrica oficial](../rubrica.md)** (PR #104) y el issue [#30](https://github.com/CarloSantoro21/trading_maxxing/issues/30). Si algo se cae en vivo, se usa el **Plan B** (video grabado) sin romper el orden.
- **Arquitectura que se enseña:** Cloudflare, plan gratuito (Pages, Workers + Hono, D1, Access, Workers Observability) y el motor en la Mac con `docker compose`. Detalle en [`docs/arquitectura/cloudflare.md`](../arquitectura/cloudflare.md).

## Antes de empezar (checklist)

- [ ] Sesión iniciada en **GitHub** (como *maintainer* del repo) y en el **dashboard de Cloudflare** (Workers & Pages, D1, Zero Trust).
- [ ] Correo del presentador en la allowlist de **Access** de dev y de prod, con el One-time PIN ya usado (cookie vigente) en las dos URLs.
- [ ] Motor corriendo en la Mac (`docker compose ps` en verde, `caffeinate` activo) y la web mostrando "motor en línea".
- [ ] Pestañas abiertas en el navegador, **en el mismo orden del guion**:
  1. GitHub, [`docs/final/README.md`](README.md) del monorepo y la Wiki.
  2. GitHub, [`CONTRIBUTING.md`](../../CONTRIBUTING.md) + diagrama [`docs/propuesta/img/branches.png`](../propuesta/img/branches.png).
  3. GitHub, vista de **PRs** con uno recién abierto (ver Paso 3).
  4. GitHub Actions, el run del pipeline del PR.
  5. **URL de dev**: `https://tm-web-dev.pages.dev` (tras Access).
  6. GitHub Actions, el run con la aprobación pendiente del *environment* `prod`.
  7. **URL de prod**: `https://tm-web.pages.dev` (tras Access).
  8. Cloudflare → Workers & Pages → `tm-api` → **Observability** (logs y trazas, última hora).
  9. Cloudflare → `tm-api` → **Metrics** (requests, errores, CPU time) y una terminal con `docker stats` de la Mac.
  10. GitHub, el repo [`trading_maxxing-infra`](https://github.com/CarloSantoro21/trading_maxxing-infra).
- [ ] Red estable.
- [ ] **Plan B general:** video grabado de 10 min con los 10 pasos en orden, disponible offline. Se usa si cae internet, Cloudflare o GitHub, o si el pipeline falla por una razón ajena al código.
- [ ] **Plan B parcial:** si solo cae el deploy a dev, se muestra el run anterior exitoso y se narra el pipeline sobre él.

## Storyboard (10 pasos del orden de evaluación)

| # | Tiempo | Paso (rúbrica §2) | Qué se muestra | Speaker notes |
|---|---|---|---|---|
| **1** | **0:00 – 1:00** | **Descripción del proyecto y tecnología** | Wiki (Home y Arquitectura) + diagrama de [infraestructura](../propuesta/img/infra.png) | «trading_maxxing es una plataforma multiestrategia para < 10 usuarios. Combina Kronos y estrategias validadas sobre NautilusTrader en Binance Testnet. Todo lo público vive en Cloudflare, plan gratuito: Pages, Workers con Hono, D1, Access y Observability. El motor corre en una Mac con Docker porque Workers no puede correr NautilusTrader ni PyTorch. El LLM es opcional y está apagado. No es asesoría financiera.» |
| **2** | **1:00 – 1:45** | **Estrategia de ramas** | [`CONTRIBUTING.md`](../../CONTRIBUTING.md) + diagrama [`branches.png`](../propuesta/img/branches.png) | «Git Flow simplificado: `feature/*` → `develop` → `main`. PRs con squash a `develop`, merge a `main`. `develop` y `main` protegidas; PR requiere CI en verde.» |
| **3** | **1:45 – 3:00** | **Cambio en el código visible en la UI** | Editor con un cambio preparado (texto del hero del dashboard) en una rama `feature/demo-<fecha>` | Mostrar el diff, commitear y empujar: `git push -u origin feature/demo-<fecha>`. «Un cambio pequeño en el hero para identificarlo en Dev y en Prod.» |
| **4** | **3:00 – 4:00** | **Abrir el PR, aprobarlo y hacer el merge a `develop`** | GitHub, pestaña *Pull requests* | `gh pr create --base develop --title "demo: cambio visible en la UI"`. Mostrar los checks corriendo, esperar el verde (o enseñar uno pre-aprobado si tarda), aprobar y hacer *squash merge*. |
| **5** | **4:00 – 6:00** | **Pipeline de dev: build, pruebas unitarias, cobertura, deploy, UAT** | GitHub Actions, run del push a `develop` | Mostrar en orden: **(a)** `ruff` y `pytest --cov` (≥ 70 %), **(b)** `vitest --coverage` de `apps/api` con el reporte JUnit en *Checks* y la cobertura en el *job summary*, **(c)** imágenes del motor a Docker Hub, **(d)** `wrangler d1 migrations apply` + `wrangler deploy --env dev` y `wrangler pages deploy` a `tm-web-dev`, **(e)** UAT con Playwright contra dev. «Pruebas unitarias con cobertura, deploy a dev y UAT: los 10 pts del pipeline del backend.» |
| **6** | **6:00 – 6:40** | **Abrir la URL de dev y ver el cambio** | `tm-web-dev.pages.dev` | Refrescar; el cambio del Paso 3 es visible y el header dice **DEV** con el SHA corto. «Pages dev, detrás de Access; `/api/*` va al Worker `tm-api-dev` por service binding.» |
| **7** | **6:40 – 8:10** | **PR `develop` → `main`, aprobar environment prod, ver el cambio en la URL pública** | GitHub: PR `develop` → `main`; Actions: aprobación de `prod`; navegador: `tm-web.pages.dev` | `gh pr create --base main --head develop --title "release: demo"`. Al mergear, el pipeline **queda esperando aprobación** en el environment `prod` (*required reviewer*). Aprobar en vivo; ver el deploy terminar y refrescar prod: aparece el cambio y el header dice **PROD**. |
| **8** | **8:10 – 9:30** | **APM: requests, trazas, error y logs** | Cloudflare → `tm-api` → Observability | **(a)** logs de requests de la última hora (JSON con `request_id`, ruta, status y ms), **(b)** una traza de `GET /api/dashboard`, **(c)** disparar el error de demo como admin (`POST /api/admin/debug/error`, issue [#29](https://github.com/CarloSantoro21/trading_maxxing/issues/29)), **(d)** buscar su `request_id` y abrir el log de la excepción con `error_name`, mensaje y stack. «Requests, trazas y un error con mensaje: lo que pide la rúbrica.» |
| **9** | **9:30 – 10:30** | **Métricas: CPU, memoria y requests** | Cloudflare → `tm-api` → Metrics + terminal con `docker stats` | Gráficas de **requests**, **errores** y **CPU time** del Worker (últimos 30 min). «Workers no expone memoria: es un isolate de 128 MB. La memoria y CPU del motor se ven en la Mac con `docker stats`; con Cloudflare Containers se verían por contenedor en el mismo panel.» |
| **10** | **10:30 – 11:30** | **Terraform: repo `trading_maxxing-infra`** | GitHub, repo [`trading_maxxing-infra`](https://github.com/CarloSantoro21/trading_maxxing-infra) | Mostrar `envs/dev/`, `envs/prod/` y los recursos: D1, proyecto de Pages y Access (app web + service token del motor). «Provider `cloudflare/cloudflare`, dos stacks, outputs que consume el pipeline.» |
| cierre | **11:30 – 12:00** | Cierre | [`docs/estado-del-proyecto.md`](../estado-del-proyecto.md) | «Costo en la nube: 0 USD. Cuando haya presupuesto, el motor pasa a Cloudflare Containers con las mismas imágenes. Gracias.» |

## Qué mostrar al mover el ratón

- **Paso 3 (código):** señalar el archivo y la línea exacta que se cambia; commit corto en Conventional Commits.
- **Paso 5 (pipeline):** enfocar el *summary* del run; abrir la pestaña *Checks* con el reporte JUnit y la tabla de cobertura.
- **Paso 6 (dev):** DevTools → *Network*: los `fetch` van a `/api/*` del mismo dominio (no hay CORS ni otra URL).
- **Paso 7 (prod):** destacar el banner "Environment `prod` waiting for review" y luego el deploy verde.
- **Paso 8 (APM):** copiar el `request_id` de la respuesta del error y pegarlo en el buscador de Observability.
- **Paso 9 (métricas):** generar unas requests antes para que la gráfica no esté plana.
- **Paso 10 (Terraform):** si hay tiempo, mostrar el `plan` de un PR reciente del repo de infra.

## Fallas previstas y mitigaciones

| Falla | Cómo se verá | Mitigación en vivo |
|---|---|---|
| Runners de GitHub Actions saturados | El run no inicia | Mostrar el **run anterior** verde del mismo workflow; narrar sobre él. |
| Deploy tarda | El Paso 6 o 7 bloquea el guion | Saltar a los Pasos 8 y 9 y volver al 6/7 cuando termine. |
| Aprobación de `prod` no refresca | No aparece "Review deployments" | Refrescar la página o pedir al segundo revisor que apruebe desde su cuenta. |
| Access pide PIN de nuevo | Pantalla de login de Access | Tener el correo abierto en otra pestaña para copiar el código; o entrar antes de la demo. |
| El log del error no aparece | Retraso de ingesta | Disparar el error durante el Paso 7 y mostrar uno anterior mientras llega. |
| Métricas planas | No hay tráfico | Lanzar unas requests (`hey` o un `for` con `curl`) **antes** del Paso 9. |
| La web dice "motor desconectado" | La Mac se durmió | Despertarla; `docker compose up -d`. No afecta a los criterios de la rúbrica. |
| **Cae internet o Cloudflare** | Nada carga | **Plan B: video grabado** completo con los 10 pasos (grabado la víspera, en el escritorio). |

## Q&A: preguntas probables y respuestas cortas

- **¿Por qué solo spot y long?** Binance Testnet es spot y queremos reducir superficie: `long` = BTC/ETH comprado, `flat` = USDT. Sin cortos.
- **¿Cómo validan que no hay look-ahead?** El `AgentContext` arma solo con velas **cerradas**; walk-forward con tramos disjuntos y Monte Carlo sobre los retornos out-of-sample. Kronos se evalúa solo después de junio de 2024.
- **¿Qué pasa si una estrategia se retira con posición abierta?** El `LifecycleMonitor` cambia el estado; el BotRunner emite la venta y el motor la ejecuta aunque el sleeve esté retirado. Prueba: `services/engine/tests/test_orchestrator_hardening.py`.
- **¿Cómo aíslan usuarios entre sí?** D1 no tiene RLS: cada consulta de la API filtra por el usuario autenticado por Access y lo ajeno responde 404. Hay pruebas de eso en `apps/api`.
- **¿Por qué el motor no está en la nube?** Workers corre Python en Pyodide, sin paquetes nativos (NautilusTrader, PyTorch), sin procesos permanentes, con 128 MB y 10 ms de CPU por invocación en el plan gratuito. Con Workers Paid (5 USD al mes) se movería a Cloudflare Containers con las mismas imágenes ([`decisiones.md`](decisiones.md) ADR #12).
- **¿Por qué Cloudflare y no Azure o AWS?** La rúbrica acepta Azure o Cloudflare; Cloudflare cubre todo lo público en el plan gratuito y sin crédito que se acabe. Historia completa en las ADR #4, #11 y #12.
- **¿Cómo se conecta el motor si no expone puertos?** Solo hace conexiones salientes: a Binance y a `/internal/*` del Worker con un service token de Access más `X-Service-Token`. Los comandos (`start`, `stop`, `kill`) los lee por *polling*.
- **¿Y el LLM?** Opcional y apagado por defecto (ADR #13). Si se prende, el `NewsAgent` veta por noticias y es fail-open: nunca bloquea una operación por estar caído.
- **¿Por qué 70 % de cobertura?** Techo realista para el calendario; la parte crítica (orquestador, riesgo, motor, API) tiene más.
- **¿Qué falta?** Lista en [`docs/estado-del-proyecto.md`](../estado-del-proyecto.md).

## Después de la demo

- Dejar abiertas las pestañas 8 y 9 (Observability y Metrics) por si hay preguntas de observabilidad.
- Tener a mano [`docs/final/README.md`](README.md), [`docs/final/arquitectura.md`](arquitectura.md), [`docs/arquitectura/cloudflare.md`](../arquitectura/cloudflare.md) y [`docs/final/decisiones.md`](decisiones.md) (ADR #12).
- No hay nada que destruir: el plan gratuito de Cloudflare no cobra.

## Ensayo (pendiente de equipo)

Este guion es el artefacto. El **ensayo en vivo** queda fuera de este issue técnico: al menos **dos pasadas completas con cronómetro** y el video del Plan B grabado y accesible offline antes del día de la entrega.
