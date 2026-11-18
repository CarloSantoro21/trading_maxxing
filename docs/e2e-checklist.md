# Checklist de pruebas end-to-end (#28)

Este documento cubre dos cosas separadas:

1. **UAT automática contra dev** con Playwright: corre en el pipeline después del deploy
   a dev y bloquea la promoción a prod si falla. Sección *UAT automatizada*.
2. **Checklist manual del flujo completo contra Binance Testnet** (señal → orden →
   trade visible): sirve para grabar el video de demostración y para validar visualmente
   el stack local. Sección *Checklist manual (Binance Testnet)*.

---

## UAT automatizada (Playwright contra dev)

El spec vive en `apps/web/tests/e2e/uat.spec.ts` y recorre:

- `GET $API_URL/health` (si `API_URL` está configurada).
- Login con el usuario demo.
- Onboarding (capital, nivel de riesgo, metas) si el perfil todavía no existe.
- Dashboard: el encabezado *Mi portafolio* y la tarjeta *Bot* están presentes.
- `/strategies`: la página carga el catálogo compartido.

Si algo falla, el job `uat` del workflow `.github/workflows/uat.yml` termina rojo y el
job `promote-to-prod` no corre (`needs: uat`). El reporte HTML y el JUnit se suben como
artifact `playwright-report`.

### Variables y secrets

| Variable       | Dónde                                      | Ejemplo                                 |
| -------------- | ------------------------------------------ | --------------------------------------- |
| `APP_URL`      | Secret de GitHub Actions / env local       | `https://dev.trading-maxxing.pages.dev` |
| `API_URL`      | Secret (opcional: habilita `/health` check)| `https://api.dev.trading-maxxing.xyz`   |
| `DEMO_EMAIL`   | Secret                                     | `demo@trading-maxxing.local`            |
| `DEMO_PASSWORD`| Secret                                     | `demo1234` (del seed; rotar en nube)    |

**Nunca** commitear credenciales. En GitHub: *Settings → Secrets and variables → Actions*.
En local:

```bash
cd apps/web
APP_URL=https://dev.<...> \
API_URL=https://api.dev.<...> \
DEMO_EMAIL=demo@trading-maxxing.local \
DEMO_PASSWORD=demo1234 \
  pnpm e2e:uat
```

Sin `APP_URL` en local, `pnpm e2e:uat` hace `test.skip` con un mensaje claro y termina
en verde. En CI, si `APP_URL` falta, el workflow avisa en el *Step Summary* y sale en
verde sin verificar nada (el fallo real lo detecta el gate `needs: uat` del pipeline a
prod).

### Estado del pipeline

El workflow está como `workflow_dispatch` y escucha también `workflow_run` del único
job que hoy publica el frontend (*Deploy web (Cloudflare Pages)*). Cuando aterrice el
pipeline de deploy completo a dev (#27, #45) hay que:

1. Cambiar el `workflows:` del `workflow_run` por el nombre del workflow de deploy-a-dev.
2. Mover (o duplicar) el job `promote-to-prod` al workflow de deploy-a-prod, con
   `needs: uat` desde este workflow o con otro `workflow_run` encadenado.

---

## Checklist manual (Binance Testnet)

Guía para validar el flujo completo **con dinero falso en Binance Testnet**, usado para
grabar el video de demostración o depurar visualmente el stack local. Sustituye a la
parte "live" del spec anterior, que ahora corre automatizada contra dev (sin testnet).

### Requisitos previos

- [ ] Node 24 instalado (`.nvmrc`).
- [ ] Cuenta en <https://testnet.binance.vision> con API key + secret creados.
- [ ] Supabase CLI instalado y con `supabase start` corriendo (API en `http://127.0.0.1:54321`).
- [ ] `cp .env.example .env` en la raíz con `BINANCE_API_KEY` / `BINANCE_API_SECRET` de
      testnet y `BINANCE_TESTNET=1` (nunca llaves de producción) y
      `cp apps/web/.env.example apps/web/.env.local` con la llave `anon` de
      `supabase status`.
- [ ] `make up` levantó Redis, `api`, `agents`, `engine` y (en Linux/Intel) `forecast`.
      En Mac con MPS: `make up-mps` + `make forecast-mps` en otra terminal.
- [ ] Usuario demo creado por el seed (o `make user EMAIL=... ROLE=member`).
- [ ] Frontend en vivo: `make web` (o `pnpm dev` dentro de `apps/web`).

### 1. Login y onboarding

- [ ] Abrir <http://localhost:3000/> y clic en *Iniciar sesión*.
- [ ] Capturar `demo@trading-maxxing.local` / `demo1234` y enviar.
- [ ] Sin perfil previo, redirige a `/onboarding`.
- [ ] Paso 1 *Capital*: escribir `1000` USDT.
- [ ] Paso 2 *Nivel de riesgo*: dejar en 3 (Moderado) o elegir otro; verificar que la
      explicación muestra la caída máxima en USDT.
- [ ] Paso 3 *Metas*: agregar al menos una (p. ej. 15 % a fecha futura) y guardar.
- [ ] Aparece la confirmación ("¡Perfil guardado!"); *Ir a mi dashboard* lleva a
      `/dashboard`.

### 2. Crear y arrancar el bot

- [ ] En `/dashboard`, la tarjeta *Bot* muestra *Aún no tienes bot* y el botón
      *Crear mi bot*.
- [ ] Al crearlo aparece la etiqueta *Detenido* y los controles *Arrancar / Detener /
      Paro de emergencia*.
- [ ] Pulsar *Arrancar*; la etiqueta cambia a *Operando* sin recargar la página.

### 3. Señal (publicación por los agentes)

- [ ] En `make logs agents` aparece un mensaje `agent=<trend|meanrev|ml>` publicando una
      señal con `symbol=BTCUSDT|ETHUSDT`, `side=buy|sell` y una confianza.
- [ ] En Redis (`make redis-cli`) el stream `tm:signals` tiene la entrada nueva
      (`XLEN tm:signals`).
- [ ] Si no llegan señales en la ventana esperada: revisar `OFFLINE=0` en `.env` y que
      los agentes tengan el símbolo habilitado.

### 4. Decisión y orden a Binance Testnet

- [ ] `make logs engine` muestra la decisión del guardia (riesgo, sizing) y, si aprueba,
      una orden enviada a Binance Testnet (`POST /api/v3/order` con `symbol`, `side`,
      `type=MARKET|LIMIT`, `quantity`).
- [ ] La respuesta de Binance Testnet incluye `orderId` y `status=FILLED`
      (o `PARTIALLY_FILLED`).
- [ ] En <https://testnet.binance.vision/en/trade> (con la misma API key) la orden
      aparece en *Trade history*.

### 5. Trade visible en el dashboard

- [ ] En la tarjeta *Últimas operaciones* del dashboard aparece una fila con:
      - Pastilla *Compra* o *Venta*.
      - Símbolo (`BTC/USDT` o `ETH/USDT`).
      - Cantidad y precio de entrada.
      - Enlace *¿Por qué?* que despliega la justificación (señal + simulación + veto).
- [ ] En la tarjeta *Equity* la curva suma un nuevo punto (requiere dos o más
      snapshots; puede tomar un par de minutos).
- [ ] La métrica *Equity* refleja `capital + P&L no realizado`.
- [ ] El badge *Live* en la esquina superior derecha indica que Realtime está conectado.

### 6. Cierre y limpieza

- [ ] *Detener* el bot desde el dashboard: la etiqueta vuelve a *Detenido* y no se
      envían órdenes nuevas.
- [ ] *Paro de emergencia* cierra las posiciones abiertas y marca el bot como *Paro de
      emergencia* (todas las posiciones vendidas a USDT en testnet).
- [ ] `make down` detiene los contenedores pero conserva el estado del volumen de Redis.
- [ ] `supabase stop` termina el stack de Supabase local.

### Video de demostración

- Grabar una corrida completa (recomendado: QuickTime o Loom, resolución 1280x720,
  10-15 min) con narración breve en cada paso.

**Pendiente: subir video.** Reemplazar esta línea con la URL cuando esté disponible:

```
https://<pendiente-de-subir>
```

---

## Automatización local complementaria

- `cd apps/web && pnpm e2e:mocked` repite los pasos 1-5 contra respuestas mockeadas
  con `page.route()` — no necesita Supabase ni Docker; corre en el CI del PR como
  gate rápido.
- `cd apps/web && pnpm e2e:uat` corre la UAT contra el deploy de dev con las variables
  `APP_URL`, `DEMO_EMAIL`, `DEMO_PASSWORD` y, opcionalmente, `API_URL`.
