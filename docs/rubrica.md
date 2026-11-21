# Rúbrica del proyecto final

Rúbrica oficial del curso (Infraestructura para el Desarrollo Continuo, ITESO). **Todo issue y todo PR de la recta final debe servir a algún punto de esta lista.** Entrega y demo: **6 de diciembre de 2026**.

> **Consecuencia principal:** la rúbrica solo acepta **Azure Portal o Cloudflare** como nube y evalúa las métricas del **App Service Plan**. Por eso, desde el 17 de noviembre de 2026, el backend va en **Azure App Service** (con Application Insights para el APM) y el frontend en **Cloudflare Pages**. El Terraform de AWS que está en `infra/` (PR #79) queda obsoleto. El deploy ya **no** depende del go/no-go (#44): es obligatorio.

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

Estado al 17 de noviembre de 2026.

| Criterio | Pts | Estado | Issues |
|---|---|---|---|
| Wiki | 10 | 🟡 PR #99 abierto. Hay que cambiar los diagramas de infraestructura y de deployment a Azure + Cloudflare. **Ya vamos tarde** (el nivel de 10 pts era a mitad del curso) | #7 |
| Plan de trabajo | 5 | 🟡 Hay issues, milestones y un Project Board, pero falta la jerarquía PBI → Story → Task | #103 |
| Ambientes | 10 | ❌ No hay nada desplegado. Hacen falta dev y prod para el backend (App Service) y el frontend (Cloudflare Pages) | #26, #27, #45 |
| Estrategia de ramas | 10 | 🟢 `feature/*` → `develop` → `main` con PRs. Falta la protección de ramas | #103 |
| Pipeline del backend | 10 | 🟡 La cobertura ya está (`pytest --cov` en `ci.yml`). Faltan la UAT, que los resultados se vean en el run y el deploy a dev y prod | #27, #28 |
| Pipeline del front | 10 | 🟡 El export estático y el workflow manual ya están (#77). Faltan los dos ambientes | #45 |
| Cambio visible en Dev | 10 | ❌ Depende de los ambientes | #45, #30 |
| Aprobación a Prod | 5 | ❌ GitHub Environment `prod` con *required reviewers* | #27, #45 |
| Cambio en Prod | 10 | ❌ Depende de los ambientes | #27, #45 |
| APM | 10 | ❌ Application Insights con OpenTelemetry y un endpoint de error para la demo | #29 |
| Métricas | 5 | ❌ Dashboard con CPU y memoria del App Service Plan | #29 |
| Terraform | 5 | 🟡 Hay Terraform, pero es de AWS y está en el monorepo. Hay que rehacerlo con `azurerm` en el repo separado `trading_maxxing-infra` | #26, #39 |

**Ruta crítica (≈ 65 pts dependen de esto):** cuenta de Azure for Students → #26 (Terraform en Azure) → #27 y #45 (pipelines con dev y prod) → #29 (APM y métricas) → #30 (ensayo de la demo).

Lo que no da puntos en la rúbrica (estrategias nuevas, go/no-go #44, explainer #37, caché de Kronos #62) tiene menos prioridad que la ruta crítica.

## 5. Arquitectura de despliegue elegida

```
GitHub (monorepo trading_maxxing)               GitHub (trading_maxxing-infra)
  feature/* ─PR─▶ develop ─PR─▶ main               Terraform azurerm: envs/dev, envs/prod
        │             │            │                       │
        ▼             ▼            ▼                       ▼
   CI (lint,    deploy dev     aprobación ──▶ deploy prod   Resource Group, App Service Plan (B1),
   pruebas,     automático     (Environment               Web Apps (api, agents), App Insights,
   cobertura)   + UAT          "prod")                    Log Analytics, Redis
        │
        └──▶ Docker Hub (imágenes por SHA) ──▶ Azure App Service (backend dev / prod)
             Cloudflare Pages (frontend dev / prod)
             Supabase Cloud, plan gratuito (un proyecto dev y otro prod)
```

Cuenta: **Azure for Students** con el correo del ITESO (100 USD de crédito, sin tarjeta).
