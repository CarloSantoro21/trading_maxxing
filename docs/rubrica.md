# Rúbrica del proyecto final

Rúbrica oficial del curso (Infraestructura para el Desarrollo Continuo, ITESO). **Todo issue y todo PR de la recta final debe servir a algún punto de esta lista.** Entrega y demo: **6 de diciembre de 2026**.

> **Consecuencia principal:** la rúbrica solo acepta **Azure Portal o Cloudflare** como nube. Desde el 21 de noviembre de 2026 la arquitectura final es **todo en Cloudflare, plan gratuito** (Pages, Workers + Hono, D1, Access y Workers Observability), sin Supabase, con el **motor en la Mac de Carlo** (docker compose, Docker Hub y Watchtower). Sustituye a la de Azure App Service del 17 de noviembre; el Terraform de AWS en `infra/` (PR #79) queda obsoleto. El deploy **no** depende del go/no-go (#44): es obligatorio. Detalle en [`docs/arquitectura/cloudflare.md`](arquitectura/cloudflare.md).

## 1. Qué hay que entregar

1. Tarea de "presentación del proyecto final" (ya entregada).
2. Acceso al proyecto en **Azure DevOps o GitHub** (usamos GitHub) con:
   - **Backlog** en los tableros.
   - **Wiki** (archivos MD) con:
     - Descripción del proyecto
     - Componentes y sus métodos (de preferencia, diagrama de clases)
     - Stack tecnológico
     - Recursos que se van a crear (diagrama de infraestructura)
     - Diagrama de deployment
   - **Repositorio de infraestructura** con el código de Terraform que crea la infraestructura.
   - Repo o repos con los proyectos:
     - Backend, con PRs (usando la estrategia de ramas)
     - Frontend, con PRs (usando la estrategia de ramas)
   - **Pipelines**
     - Backend: build, pruebas unitarias, ambiente Dev y ambiente Prod
     - Frontend: ambiente Dev y ambiente Prod
   - **Docker Hub**
   - **Service connection**
   - **Azure Portal o Cloudflare**, con los recursos creados del backend y del frontend.

Entregables: la tarea de presentación (ya entregada) y la URL del proyecto en Azure DevOps o GitHub. El proyecto puede ser individual o por equipos.

Íconos para los diagramas: [AWS Architecture Icons](https://aws.amazon.com/architecture/icons/) y [Azure icons (Azure Architecture Center)](https://learn.microsoft.com/azure/architecture/icons/).

## 2. Qué se evalúa en la presentación (en este orden)

1. Descripción del proyecto
2. Descripción de la tecnología
3. Estrategia de ramas usada
4. Hacer un cambio en el código
5. Autorizar el PR
6. Ejecutar el pipeline en el ambiente de desarrollo (Dev)
7. Ver el resultado de las pruebas unitarias
8. Ver el proyecto publicado en el ambiente Dev
9. Aprobar el pipeline al ambiente de producción (Prod)
10. Ver el cambio en producción en la URL pública
11. APM: trazas, errores y mensajes
12. Métricas en el portal de Azure o de Cloudflare: CPU, memoria y requests

## 3. Rúbrica (100 pts)

| Criterio | Pts | Niveles |
|---|---|---|
| Documentación de la propuesta en la Wiki | 10 | 10: entregada a mitad del curso · 5: entregada al final |
| Plan de trabajo en el tablero | 5 | 5: PBI, stories y tareas · 3: issues y tareas · 0: elementos no relacionados con los commits |
| Ambientes para backend y frontend | 10 | 10: dev y prod en los dos repos · 7: los dos ambientes en al menos un repo · 3: al menos un ambiente en al menos un repo |
| Estrategia de ramas | 10 | 10: evidencia de ramas y PRs · 5: commits directos a main o master |
| Pipeline del backend | 10 | 10: UAT y cobertura · 5: UAT o cobertura · 2: build completo sin pruebas |
| Pipeline del front | 10 | 10: corre en los dos ambientes · 4: corre en al menos un ambiente |
| Se ve el cambio abriendo la URL del front en Dev | 10 | 10: se manda el PR al primer ambiente y el cambio se ve bien en Dev · 0: no |
| Aprobar el paso a producción | 5 | 5: se aprueba después del primer ambiente · 0: no |
| Se muestra el cambio en producción | 10 | 10: sí · 0: no |
| Monitoreo APM | 10 | 10: se muestra al menos un mensaje de error o una traza · 5: solo se ven las requests |
| Monitoreo de métricas | 5 | 5: se muestran CPU y memoria del App Service Plan · 0: no |
| Terraform | 5 | 5: repo de infraestructura con proyecto de Terraform · 0: no |

## 4. Cómo cubrimos cada criterio

Estado al 21 de noviembre de 2026. Arquitectura: Cloudflare (plan gratuito) + motor en la Mac (sección 5).

| Criterio | Pts | Cómo lo cubrimos | Estado | Issues |
|---|---|---|---|---|
| Wiki | 10 | Páginas en `docs/wiki/` (descripción, clases, stack, infraestructura, deployment, ramas, board) publicadas con `scripts/publish_wiki.sh`; diagramas de infraestructura y deployment regenerados para Cloudflare + Mac | 🟡 Páginas y diagramas listos; falta publicar en la Wiki de GitHub. **Ya vamos tarde** (el nivel de 10 pts era a mitad del curso) | #7 |
| Plan de trabajo | 5 | Project Board con jerarquía PBI → Story → Task ligada a los commits | 🟡 Hay issues, milestones y board; falta la jerarquía | #103 |
| Ambientes | 10 | dev y prod en backend (Workers `tm-api-dev` / `tm-api` + D1 `tm-dev` / `tm-prod`) y frontend (Pages `tm-web-dev` / `tm-web`); Terraform crea D1, Pages y Access | 🟡 La API ya existe (#120); faltan el `apply` de Terraform y los deploys | #26, #27, #45, #115, #116, #117 |
| Estrategia de ramas | 10 | `feature/*` → `develop` → `main` con PRs | 🟢 Evidencia de ramas y PRs; falta la protección de ramas | #103 |
| Pipeline del backend | 10 | `pytest --cov` (Python) y `vitest --coverage` de `apps/api` con JUnit en Checks y cobertura en el *job summary*; UAT con Playwright contra dev; deploy del Worker y migraciones de D1 a dev y prod; imágenes del motor a Docker Hub | 🟡 Cobertura y UAT existen (#106, `uat.yml`); falta el deploy a Cloudflare | #27, #28 |
| Pipeline del front | 10 | `wrangler pages deploy` a `tm-web-dev` (merge a `develop`) y `tm-web` (merge a `main` con aprobación) | 🟡 Export estático y workflow manual listos (#77); falta el frontend sin Supabase y los dos ambientes | #45, #117 |
| Cambio visible en Dev | 10 | Indicador de SHA y ambiente en el header; URL de `tm-web-dev` tras Access | ❌ Depende de los ambientes | #45, #30 |
| Aprobación a Prod | 5 | GitHub Environment `prod` con *required reviewers* | ❌ Por configurar | #27, #45 |
| Cambio en Prod | 10 | URL pública de `tm-web` tras Access | ❌ Depende de los ambientes | #27, #45 |
| APM | 10 | Workers Observability: logs estructurados, trazas y el error de demo `POST /api/admin/debug/error` | 🟡 `[observability]` y el endpoint ya están en `apps/api` (#120); falta verlo en la nube | #29 |
| Métricas | 5 | Panel del Worker: requests, errores, CPU time y duración. Workers no reporta memoria: se complementa con `docker stats` de la Mac (o métricas de Cloudflare Containers si se paga) | ❌ Hay que confirmar con el profe si basta CPU + requests | #29 |
| Terraform | 5 | Repo separado `trading_maxxing-infra` con `cloudflare/cloudflare`: D1, Pages y Access (app web + service token del motor) por ambiente | 🟡 Código listo; falta el `apply`, el estado remoto y el CI del repo | #26, #39, #109 |

**Ruta crítica (≈ 65 pts dependen de esto):** #26 (Terraform: D1, Pages, Access) → #115/#117 (API y frontend sin Supabase) → #27 y #45 (pipelines con dev y prod) → #29 (APM y métricas) → #30 (ensayo de la demo). #116 (motor en la Mac) es necesario para que el dashboard muestre trades reales, pero no bloquea los criterios de la rúbrica.

#118 (PR #119) deja el LLM y las noticias **opcionales y apagados por defecto**: no da puntos por sí mismo, pero quita llaves y costos de terceros del camino de la demo.

Lo que no da puntos en la rúbrica (estrategias nuevas, go/no-go #44, auditor #108, que está congelado) tiene menos prioridad que la ruta crítica.

## 5. Arquitectura de despliegue elegida

```
GitHub (monorepo trading_maxxing)                     GitHub (trading_maxxing-infra)
  feature/* ─PR─▶ develop ─PR─▶ main                     Terraform cloudflare/cloudflare
        │             │            │                     envs/dev, envs/prod:
        ▼             ▼            ▼                     D1, Pages, Access (web + motor)
   CI (lint,     deploy dev    aprobación ──▶ deploy prod
   pruebas,      automático    (Environment
   cobertura)    + UAT         "prod")
        │
        ├──▶ Cloudflare Workers tm-api-dev / tm-api (Hono) + D1 tm-dev / tm-prod
        ├──▶ Cloudflare Pages tm-web-dev / tm-web (tras Access; /api/* por service binding)
        └──▶ Docker Hub (agents, engine, forecast) ──▶ Watchtower en la Mac de Carlo
                                                     (solo conexiones salientes a Binance
                                                      Spot Testnet y a /internal/* del Worker)
```

- **Cuenta:** Cloudflare, plan gratuito (0 USD). APM y métricas con Workers Observability.
- **Por qué el motor no va en Workers:** Python en Pyodide sin paquetes nativos (NautilusTrader, PyTorch), sin procesos permanentes, 128 MB y 10 ms de CPU por invocación en el plan gratuito.
- **Cuando paguemos:** Cloudflare Containers (Workers Paid, 5 USD al mes) con las mismas imágenes; ver [`docs/arquitectura/cloudflare.md`](arquitectura/cloudflare.md).
