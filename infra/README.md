# infra/: AWS con Terraform

Infraestructura como código de trading_maxxing (#26, #39 y las alarmas de #29).

> **Estado: solo código.** Todavía no hay cuenta de AWS, así que nada de esto se ha
> aplicado. Lo que sí está verificado es que `terraform fmt -check -recursive` y
> `terraform validate` pasan en `bootstrap`, `envs/staging` y `envs/prod`, y el workflow
> `.github/workflows/terraform.yml` lo repite en cada PR que toca `infra/`. El despliegue
> queda para la entrega final (etiqueta `deploy-diferido`; ver la lista de
> [pendientes](#pendientes-para-cuando-haya-cuenta)).

## Estructura

```
infra/
├── bootstrap/            # Una vez por cuenta: estado (S3 + DynamoDB), budget y OIDC
├── envs/
│   ├── staging/          # Bajo demanda; se destruye al terminar
│   └── prod/             # 24/7 durante la demo
├── modules/
│   ├── network/          # VPC, 2 subredes públicas + 2 privadas, SIN NAT Gateway
│   ├── alb/              # ALB público: /api/* -> api, todo lo demás -> 404
│   ├── ecs/              # Cluster Fargate ARM64, 5 servicios y Service Connect
│   ├── secrets/          # Secrets Manager (Binance Testnet, Supabase, Groq, CryptoPanic)
│   ├── logs-alarms/      # Log groups, SNS por correo y alarmas de CloudWatch
│   ├── s3/               # Bucket de artefactos
│   ├── budget/           # AWS Budget (50 USD)
│   └── github-oidc/      # Proveedor OIDC y roles de GitHub Actions
└── .tflint.hcl
```

`envs/staging` y `envs/prod` tienen exactamente los mismos `main.tf`, `variables.tf` y
`outputs.tf`. Lo único que cambia es `backend.tf` (la llave del estado) y
`env.auto.tfvars` (CIDR, retención de logs, protecciones contra borrado, etc.).

## Arquitectura

```
                 Cloudflare Pages (frontend estático, #45)
                              │  fetch https://<alb>/api/...
                              ▼
 Internet ──► ALB (subredes públicas, :80/:443) ── /api/* ──► api (:8000)
                                                             │
 ┌──────────────── VPC 10.x.0.0/16, ECS Fargate ARM64 (Graviton) ─────────────────┐
 │  subredes públicas (2 AZ), cada tarea con IP pública y SIN entrada desde internet │
 │                                                                                │
 │   api ──┐                                                                      │
 │   agents ├── Service Connect ──► redis:6379   (redis:7.4-alpine, sin persistencia)
 │   engine ┘        (Cloud Map)  ──► forecast:8001 (Kronos en CPU)                  │
 │                                                                                │
 │  subredes privadas (2 AZ): sin salida a internet, reservadas                    │
 └────────────────────────────────────────────────────────────────────────────────┘
        │ HTTPS saliente (443): Docker Hub, Binance Testnet, Supabase, Groq,
        │ CryptoPanic, Hugging Face y las APIs de AWS
        ▼
 Secrets Manager · CloudWatch Logs/alarmas ──► SNS (correo) · S3 (artefactos)
```

### Decisiones

- **Sin NAT Gateway.** Un NAT cuesta 0.045 USD/h (~32.85 USD/mes) por zona más
  0.045 USD/GB procesado; con 2 zonas son ~66 USD/mes, más que todo el Fargate de los
  servicios chicos. En su lugar las tareas corren en las subredes públicas con IP pública
  (0.005 USD/h, ~3.65 USD/mes cada una). La seguridad no depende de la subred sino de los
  security groups:
  - `app` (api, agents, engine, forecast): solo entra el ALB al 8000 y la propia app al
    8001 (forecast). Sale a 443 y a redis.
  - `redis`: solo entra `app` al 6379. Sale a 443 (pull de la imagen).
  - `alb`: entra 80/443 desde internet y sale solo al 8000 de la VPC.
  - El security group por defecto de la VPC queda sin reglas.

  Las subredes privadas existen (2) pero sin ruta a internet; sirven para recursos sin
  salida (ElastiCache o RDS más adelante, o tareas con VPC endpoints).
- **Redis como servicio de ECS, no ElastiCache.** Un `cache.t4g.micro` cuesta ~11.7 USD/mes
  y la tarea de 0.25 vCPU / 0.5 GB ~7.2 USD/mes, sin cambiar el código (`REDIS_URL` sigue
  siendo `redis://redis:6379/0`, el mismo nombre que en docker-compose). La contra: no
  persiste; si la tarea se reinicia se pierden los streams pendientes. Es aceptable porque
  la fuente de verdad es Supabase. Redis es privado: solo lo alcanza el security group de
  la app (criterio de #26).
- **Service Connect** (namespace HTTP de Cloud Map) da los nombres `redis` y `forecast`
  dentro del cluster, igual que en docker-compose (`FORECAST_URL=http://forecast:8001`).
- **ARM64 (Graviton)** en todas las tareas: ~20 % más barato que x86. Las imágenes de
  Docker Hub ya se publican multi-arch (`docker-publish.yml`).
- **engine y redis son "single"**: el deploy detiene la tarea vieja antes de arrancar la
  nueva (`minimum_healthy_percent = 0`, `maximum_percent = 100`), para que nunca haya dos
  engines mandando órdenes. Lo mismo para agents. api y forecast hacen rolling normal.
- **Circuit breaker con rollback** en todos los servicios.
- **Imágenes de Docker Hub**: `<dockerhub_username>/trading-maxxing-<servicio>:<image_tag>`,
  con `image_tag` como variable (`develop`, `sha-<commit>` o `X.Y.Z`). El pull es anónimo;
  si se topa con el rate limit, se pasa `dockerhub_credentials_secret_arn`.
- **Secretos**: Terraform crea el secreto, nunca su valor, para que las llaves no queden en
  el estado ni en el repo. ECS los inyecta como variables de entorno desde campos del JSON.
- **IAM mínimo por servicio**: un rol de tarea por servicio. Todos leen el bucket de
  artefactos; solo agents escribe en `reports/`, y solo engine puede publicar métricas, y
  únicamente en el namespace `TradingMaxxing`.

### Servicios y tamaños

Según la propuesta (sección de infraestructura):

| Servicio | vCPU | Memoria | Puerto | Expuesto a | Health check |
|---|---|---|---|---|---|
| api | 0.25 | 0.5 GB | 8000 | ALB (`/api/*`) | `GET /health` |
| agents | 0.5 | 1 GB | — | — | heartbeat `/tmp/heartbeat` |
| engine | 0.5 | 1 GB | — | — | heartbeat `/tmp/heartbeat` |
| forecast | 1 | 3 GB | 8001 | Service Connect | `GET /health` (arranque 300 s) |
| redis | 0.25 | 0.5 GB | 6379 | Service Connect | `redis-cli ping` |

Se cambian sin tocar el módulo con `service_sizes` y `desired_counts` en `env.auto.tfvars`
(`desired_counts = { forecast = 0 }` apaga un servicio sin destruirlo).

### Observabilidad (#29)

- Un log group por servicio: `/ecs/trading-maxxing/<entorno>/<servicio>` (retención de
  14 días en prod y 3 en staging).
- Tema SNS `<proyecto>-<entorno>-alerts` con suscripción por correo (`alarm_emails`; cada
  correo debe confirmar el mensaje de AWS).
- Alarmas (todas notifican al tema SNS):

| Alarma | Métrica | Dispara cuando |
|---|---|---|
| `<svc>-down` (×5) | `AWS/ECS CPUUtilization` (SampleCount) | 5 min sin datapoints: el servicio no tiene tareas corriendo |
| `alb-5xx` | `HTTPCode_ELB_5XX_Count + HTTPCode_Target_5XX_Count` | ≥ 5 respuestas 5xx en 5 min |
| `api-no-healthy-targets` | `HealthyHostCount` del target group | 3 min sin tareas sanas de la api |
| `drawdown` | `TradingMaxxing/Drawdown` (custom, de engine) | Máximo ≥ 0.10 en 5 min |
| `kill-switch` | `TradingMaxxing/KillSwitchTriggered` + filtro de logs `"kill switch"` | ≥ 1 activación en 1 min |

Se usa `CPUUtilization` con `SampleCount` en lugar de `RunningTaskCount` porque esta
última solo existe con Container Insights, que se cobra aparte.

**Contrato de las métricas custom** (lo debe publicar engine con `PutMetricData`):

- Namespace `TradingMaxxing` (variable `metric_namespace`, también en `METRICS_NAMESPACE`).
- Dimensión `Environment = staging | prod`.
- `Drawdown`: fracción entre 0 y 1 (0.12 = 12 %) del peor bot, cada minuto.
- `KillSwitchTriggered`: `1` cada vez que se activa el kill switch.

> Hoy engine **todavía no publica** estas métricas (falta el publicador con boto3; ver
> pendientes). Mientras tanto la alarma de drawdown queda en `OK` por falta de datos y la de
> kill switch depende del filtro de logs, que cuenta cualquier línea de engine o agents con
> `kill switch`. El RiskAgent ya arma el motivo `kill switch: drawdown ...`; falta
> confirmar que esa línea llegue al log.

## Costo mensual estimado

us-east-1, 730 h/mes. Precios: Fargate ARM 0.03238 USD por vCPU-h y 0.00356 USD por GB-h;
IPv4 pública 0.005 USD/h; ALB 0.0225 USD/h (~16.4 USD/mes) más LCU.

### prod 24/7

| Recurso | Cálculo | USD/mes |
|---|---|---|
| Fargate api | (0.25 × 0.03238 + 0.5 × 0.00356) × 730 | 7.21 |
| Fargate agents | (0.5 × 0.03238 + 1 × 0.00356) × 730 | 14.42 |
| Fargate engine | (0.5 × 0.03238 + 1 × 0.00356) × 730 | 14.42 |
| Fargate forecast | (1 × 0.03238 + 3 × 0.00356) × 730 | 31.43 |
| Fargate redis | (0.25 × 0.03238 + 0.5 × 0.00356) × 730 | 7.21 |
| **Subtotal Fargate** | | **74.69** |
| IPv4 públicas | 5 tareas + 2 del ALB (una por AZ) = 7 × 0.005 × 730 | 25.55 |
| ALB | 0.0225 × 730 | 16.43 |
| ALB LCU | tráfico de demo, ~0.5 LCU promedio × 0.008 × 730 | ~2.92 |
| Secrets Manager | 4 secretos × 0.40 | 1.60 |
| CloudWatch Logs | ~2 GB ingeridos × 0.50 (almacenamiento despreciable) | ~1.00 |
| CloudWatch alarmas | 11 métricas en alarmas × 0.10 (las 10 primeras son gratis en el free tier) | ~1.10 |
| Métricas custom | 3 (Drawdown, KillSwitchTriggered, KillSwitchLogged) × 0.30 | 0.90 |
| S3 artefactos | < 5 GB × 0.023 + requests | ~0.20 |
| Cloud Map (Service Connect) | namespace HTTP sin consultas DNS públicas | ~0 |
| SNS por correo | < 1,000 notificaciones gratis | 0 |
| **Total prod** | | **~124.5** |

Equivale a ~0.17 USD/h. La propuesta estimaba 100–120 USD/mes; la diferencia la explican
las IPv4 públicas (que AWS cobra desde 2024) y el ALB. Para la demo no hace falta un mes
completo: **una semana de prod cuesta ~29 USD**.

Ahorros posibles si hace falta: `desired_counts = { forecast = 0 }` y usar los pronósticos
precalculados del bucket (−31.4 USD), o bajar agents y engine a 0.25 vCPU / 0.5 GB
(−14.4 USD entre los dos).

### staging y cuenta

| Recurso | USD |
|---|---|
| staging bajo demanda (misma forma que prod, ~0.17 USD/h) | ~1.4 por cada 8 h encendido |
| Bucket del estado + tabla de lock (bootstrap) | < 0.10/mes |
| AWS Budget | 0 (las 2 primeras son gratis) |

Con prod 24/7 el presupuesto de 50 USD se alcanza en ~12 días. Es intencional: la alerta
avisa al 50 %, 80 % y 100 % del gasto real y cuando el pronóstico pasa los 50 USD, para
recordar hacer `terraform destroy` después de la demo.

## Uso

Requisitos: Terraform ≥ 1.10 (probado con 1.17.1), AWS CLI y credenciales de la cuenta.

### 1. Bootstrap (una vez por cuenta, #39)

Crea el bucket del estado (versionado, cifrado, privado y solo TLS), la tabla de DynamoDB
del lock, el presupuesto de 50 USD y, si la cuenta lo permite, el proveedor OIDC y los roles
de GitHub Actions. Usa estado local a propósito, porque es el stack que crea el backend.

```bash
cd infra/bootstrap
cp terraform.tfvars.example terraform.tfvars   # correos del budget; en Academy: enable_github_oidc = false
terraform init
terraform plan -out tfplan
terraform apply tfplan
terraform output -raw backend_config            # para el backend.hcl de cada entorno
```

Guarda el `terraform.tfstate` del bootstrap fuera del repo (o mígralo al bucket recién
creado con un bloque `backend "s3"` y `terraform init -migrate-state`). El bucket y la tabla
tienen `prevent_destroy`.

### 2. Cargar los secretos

Después del primer apply de un entorno existen los secretos vacíos. Las llaves que espera
cada uno salen en `terraform output secret_keys`:

```bash
aws secretsmanager put-secret-value --secret-id trading-maxxing/prod/binance-testnet \
  --secret-string '{"api_key":"...","api_secret":"..."}'
aws secretsmanager put-secret-value --secret-id trading-maxxing/prod/supabase \
  --secret-string '{"url":"https://<ref>.supabase.co","anon_key":"...","service_role_key":"...","jwt_secret":"..."}'
aws secretsmanager put-secret-value --secret-id trading-maxxing/prod/groq --secret-string '{"api_key":"..."}'
aws secretsmanager put-secret-value --secret-id trading-maxxing/prod/cryptopanic --secret-string '{"token":"..."}'
aws ecs update-service --cluster trading-maxxing-prod --service <svc> --force-new-deployment
```

Las tareas no arrancan mientras un secreto esté vacío: es mejor cargarlos y luego forzar el
deploy (o hacer el primer apply con `desired_counts` en 0).

### 3. Apply de un entorno

```bash
cd infra/envs/staging                     # o envs/prod
cp backend.hcl.example backend.hcl        # bucket = salida del bootstrap
cp terraform.tfvars.example terraform.tfvars   # dockerhub_username, image_tag, alarm_emails
terraform init -backend-config=backend.hcl
terraform plan -out tfplan
terraform apply tfplan
terraform output alb_dns_name             # la api queda en http://<dns>/api/...
```

`backend.hcl` y `terraform.tfvars` están en `.gitignore`; el único `.tfvars` versionado es
`env.auto.tfvars`, que no tiene secretos.

Service Connect solo registra en las tareas cliente los endpoints que ya existían cuando
arrancaron. En el primer apply, si api, agents o engine arrancan antes que redis y forecast,
hay que forzar un deploy de los clientes:

```bash
for s in api agents engine; do
  aws ecs update-service --cluster trading-maxxing-staging --service $s --force-new-deployment
done
```

Para cambiar de versión basta con `terraform apply -var image_tag=sha-<commit>`.

### 4. Destroy (ahorrar costos)

```bash
cd infra/envs/staging
terraform destroy
```

staging está configurado para borrarse sin trámites: `artifacts_force_destroy = true`
(vacía el bucket), `secrets_recovery_window_days = 0` y sin protección contra borrado en el
ALB. En prod hay que desactivar primero las protecciones (`alb_deletion_protection = false`
y `artifacts_force_destroy = true` en `env.auto.tfvars`, apply, y luego destroy). El
bootstrap se destruye al final y a mano (quitando `prevent_destroy`), solo si ya no queda
ningún entorno.

## CI (`.github/workflows/terraform.yml`)

En cada PR o push a develop/main que toca `infra/**`:

1. `terraform fmt -check -recursive`
2. `terraform init -backend=false` y `terraform validate` en `bootstrap`, `envs/staging` y
   `envs/prod`
3. `tflint` con el ruleset de AWS (no bloquea: `continue-on-error`)

Nada de lo anterior necesita credenciales. El job **plan** (staging y prod, solo en PRs)
corre `terraform plan` con el backend real y publica el plan como comentario en el PR
(criterio de #39). **Se omite** mientras no exista la variable `TF_STATE_BUCKET` y alguna
de las dos formas de autenticarse:

| Cuenta | Configuración en GitHub |
|---|---|
| Propia, con IAM | variable `AWS_PLAN_ROLE_ARN` = salida `github_plan_role_arn` del bootstrap (OIDC) |
| AWS Academy | secrets `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` y `AWS_SESSION_TOKEN` |

Además: variable `TF_STATE_BUCKET` y `DOCKERHUB_USERNAME` (variable o el secret que ya usa
`docker-publish.yml`).

### OIDC y la limitación de AWS Academy

El módulo `github-oidc` crea el proveedor OIDC de `token.actions.githubusercontent.com` y
estos roles, sin llaves de larga duración:

- `trading-maxxing-gha-plan`: lo asumen los PRs y las ramas develop y main. Solo lectura de
  la infraestructura y del lock; tiene un `Deny` explícito sobre
  `secretsmanager:GetSecretValue`.
- `trading-maxxing-gha-apply-<entorno>`: solo lo asume un job con `environment: <entorno>`
  (GitHub Environments con revisores). Administra únicamente los recursos con el prefijo
  del proyecto, en la región del proyecto; solo puede pasar los roles del proyecto a
  `ecs-tasks.amazonaws.com` y no puede modificar los roles de GitHub.

**AWS Academy (Learner Lab) no permite crear roles ni proveedores de IAM**: solo existe el
rol `LabRole` y las sesiones duran unas horas. Ahí:

1. El bootstrap se aplica con `enable_github_oidc = false`.
2. Los roles de ECS que crea `modules/ecs` (ejecución y uno por tarea) **tampoco se pueden
   crear**. Para eso está la variable `existing_role_arn`: con el ARN de `LabRole` el módulo
   no crea ningún rol de IAM y lo usa como rol de ejecución y de tarea (se pierde el
   mínimo privilegio por servicio, que en Academy no es posible).
3. En el CI se usan las credenciales temporales del lab (AWS Details → AWS CLI) como los
   secrets `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` y `AWS_SESSION_TOKEN`. Caducan con la
   sesión del lab, así que hay que actualizarlos cada vez que se inicia una.
4. Si la cuenta de Academy no deja crear el AWS Budget, se deja `budget_emails = []` (no se
   crea) y el límite lo pone el propio crédito del lab.

## Pendientes para cuando haya cuenta

- [ ] Crear la cuenta (propia o Academy) y aplicar `infra/bootstrap`.
- [ ] Configurar en GitHub `TF_STATE_BUCKET`, `DOCKERHUB_USERNAME` y `AWS_PLAN_ROLE_ARN` (o
      los secrets de Academy) para que el job de plan deje de omitirse.
- [ ] En Academy: probar `existing_role_arn = <ARN de LabRole>` (no se pudo verificar sin
      cuenta).
- [ ] Aplicar staging, cargar los secretos y validar que `terraform apply` crea el entorno
      desde cero y que la api responde en `http://<alb>/api/health` (#26).
- [ ] Publicar `Drawdown` y `KillSwitchTriggered` desde engine con boto3 (contrato arriba).
- [ ] Confirmar las suscripciones de SNS y probar cada alarma (por ejemplo,
      `desired_counts = { agents = 0 }` para la de servicio caído) (#29).
- [ ] Revisar los tamaños con métricas reales: el proxy de Service Connect usa parte de la
      CPU y memoria de cada tarea, y redis o api con 0.25 vCPU pueden quedar justos.
- [ ] Certificado de ACM para HTTPS (`certificate_arn`) y CORS de la api hacia el dominio de
      Cloudflare Pages (#45).
- [ ] Subir al bucket de artefactos los pronósticos precalculados y los datos históricos.
- [ ] Workflow manual de apply con GitHub Environments (`environment: staging|prod`).
- [ ] `terraform destroy` de prod después de la demo.
