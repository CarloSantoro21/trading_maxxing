# Checklist de pruebas end-to-end (#28)

Hay tres niveles de E2E, de más rápido a más completo:

| Prueba | Comando | Qué cubre | Dónde corre |
|---|---|---|---|
| **Mocked** | `cd apps/web && pnpm e2e:mocked` | Flujo de la UI (onboarding → bot → trade) con `/api/*` respondido por `page.route()`; sin API, motor ni Access | CI de cada PR (job *E2E mocked (Playwright)* de `frontend.yml`) |
| **Stack local real** | `make e2e-local` | Todo el sistema sin mocks: web → API (D1 local) → motor (agents + engine en Docker) → trade → dashboard | En la Mac ([`correr-local.md`](correr-local.md) §7) |
| **UAT contra dev** | `pnpm e2e:uat` | El deploy de dev en Cloudflare (Pages + service binding + Worker + Access) | Pipeline, después de cada deploy a dev; bloquea la promoción a prod |

---

## Stack local real (`make e2e-local`)

`apps/web/tests/e2e/local-stack.spec.ts`. Si el stack no está arriba lo levanta con
`make dev` (y lo apaga al final; `KEEP_UP=1` lo deja). Verifica:

1. `GET /api/health` por el proxy de `next dev`.
2. El sitio: onboarding si el usuario no tiene perfil; si no, el dashboard.
3. Crea el bot si no hay y pulsa **Arrancar** (queda *Operando*).
4. Espera hasta 6 minutos a que el motor registre una operación nueva de `demo_pulse` en
   `trades` (`GET /api/trades`).
5. El dashboard la muestra sin recargar (*Demo (pulso)*, *BTC/USDT*).
6. `GET /api/engine/status` dice `online` con `agents` y `engine`.

Sin llaves de Binance Testnet la operación es **simulada** (precio público de Binance y 0.1 %
de comisión; `rationale.execution = "sim"`); con llaves en `.env` pasa por Testnet.

---

## UAT automatizada (Playwright contra dev)

El spec vive en `apps/web/tests/e2e/uat.spec.ts` y recorre:

- `GET $APP_URL/api/health` a través de la Pages Function y el service binding.
- El sitio estático con el badge de ambiente (`EXPECTED_ENV`) y el indicador del motor.
- Si la identidad del service token está dada de alta: onboarding o dashboard y
  `/strategies`. Si no (lo normal: un service token de Access no trae correo), valida la
  pantalla de *No tienes acceso*.

Lo llama `uat.yml` como job dependiente (`workflow_call`) después del deploy a dev desde
`backend.yml` y `frontend.yml`. Si falla, el check *UAT (Playwright)* del commit queda en rojo
y el job *Gate UAT* (`uat-gate.yml`) del push a `main` bloquea el deploy a prod.

### Variables y secrets

| Variable | Dónde | Ejemplo |
|---|---|---|
| `APP_URL` | Variable `WEB_URL` del environment `dev` | `https://tm-web-dev.pages.dev` |
| `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` | Secrets del environment `dev` (service token de Access, outputs de Terraform) | — |
| `EXPECTED_ENV` | Fijo en `uat.yml` | `dev` |

No hay usuario ni contraseña: el login es Cloudflare Access. **Nunca** commitear
credenciales. En local:

```bash
cd apps/web
APP_URL=https://tm-web-dev.pages.dev CF_ACCESS_CLIENT_ID=... CF_ACCESS_CLIENT_SECRET=... \
  EXPECTED_ENV=dev pnpm e2e:uat
```

Sin `APP_URL` en local, `pnpm e2e:uat` se omite. En CI, si falta configuración, el workflow
lo avisa en el *Step Summary* y sale en verde sin verificar nada.

---

## Checklist manual (para el video de la demo)

Con el stack local (`make dev`) o contra dev en la nube. Para operar de verdad en Binance Spot
Testnet pon `BINANCE_TESTNET_API_KEY` / `BINANCE_TESTNET_API_SECRET` en `.env`
([`correr-local.md`](correr-local.md) §4).

### Requisitos previos

- [ ] `make dev` terminó con *Listo* y el encabezado dice *Motor en línea*.
- [ ] (Opcional) llaves de https://testnet.binance.vision en `.env`.

### 1. Perfil

- [ ] Abrir <http://localhost:3000/> (en la nube: login con código por correo de Access).
- [ ] `/onboarding`: capital, nivel de riesgo (la explicación muestra la caída máxima en
      USDT) y al menos una meta; *Guardar perfil* → *Ir a mi dashboard*.

### 2. Arrancar el bot

- [ ] En `/dashboard`, la tarjeta *Bot* muestra *Detenido* y los controles *Arrancar /
      Detener / Paro de emergencia* (o *Crear mi bot* si no hay).
- [ ] Pulsar *Arrancar*; la etiqueta cambia a *Operando* sin recargar.

### 3. Señal, decisión y ejecución

- [ ] `make dev-logs`: `agents` dice `bot … arrancado` y, al cierre del minuto, publica la
      señal de `demo_pulse` (y de las demás estrategias vivas en su timeframe).
- [ ] `docker compose exec redis redis-cli XLEN tm:decisions` crece.
- [ ] `engine` registra `fill simulado entry …` (sin llaves) o el fill de Nautilus contra
      Testnet (con llaves; la orden aparece en *Trade history* de testnet.binance.vision).

### 4. Trade visible en el dashboard

- [ ] *Últimas operaciones* muestra la fila: *Compra*, *BTC/USDT*, cantidad y precio, la
      estrategia (*Demo (pulso)*) y *¿Por qué?* con la señal, la simulación y el riesgo.
- [ ] A los 2 minutos llega la venta y la fila se cierra con su P&L.
- [ ] La curva de *Equity* suma un punto por minuto.

### 5. Cierre y limpieza

- [ ] *Detener* el bot: vuelve a *Detenido* y no se abren operaciones nuevas.
- [ ] *Paro de emergencia* cierra las posiciones abiertas y marca el bot como detenido por
      paro de emergencia.
- [ ] `make dev-down` apaga todo (conserva la D1 local y Redis; `make dev-reset` los borra).

### Video de demostración

- Grabar una corrida completa (QuickTime o Loom, 1280x720, 10-15 min) con narración breve.

**Pendiente: subir video.** Reemplazar esta línea con la URL cuando esté disponible:

```
https://<pendiente-de-subir>
```
