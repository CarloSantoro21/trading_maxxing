# Correr trading_maxxing en local (de punta a punta)

Esta guía levanta **todo el sistema en tu Mac** con un solo comando y te dice qué ver en cada
página. No necesitas ninguna cuenta ni llave: ni Cloudflare, ni Binance, ni LLM.

```
 Navegador ──▶ next dev :3000 ──/api/*──▶ wrangler dev :8787 (Worker Hono + D1 local)
                                                  ▲  /internal/* (X-Service-Token)
 docker compose:  agents ──tm:signals/tm:decisions (redis)──▶ engine ─┘
                  (capa compartida, SimulationGate, bots)      (fills sim o Testnet)
```

Es la misma arquitectura que en la nube ([`arquitectura/cloudflare.md`](arquitectura/cloudflare.md)):
la web llama a `/api/*` en su mismo origen, la API vive en un Worker con D1 y el motor hace
solo conexiones salientes a la API y a Binance. Lo único distinto es que en local no hay
Cloudflare Access (la API corre con `AUTH_MODE=dev` y entras como el usuario demo).

## 1. Requisitos (una sola vez)

| Qué | Cómo | Para qué |
|---|---|---|
| Docker Desktop | https://www.docker.com/products/docker-desktop | El motor (redis, agents, engine). `make dev` lo abre si está apagado. |
| Node 24 y pnpm 9 | `brew install node` y `corepack enable` | API (wrangler) y web (Next.js). |
| uv (Python 3.12) | `brew install uv` | Pruebas de Python y el motor nativo (opcional). |
| make, curl, git | vienen con las Command Line Tools de Xcode | |

La primera vez `make dev` instala las dependencias de `apps/api` y `apps/web` y construye las
imágenes de Docker del motor (unos 5 a 10 minutos); después arranca en menos de un minuto.

## 2. Levantar todo: `make dev`

```bash
make dev
```

Hace, en orden (el script es [`scripts/dev/up.sh`](../scripts/dev/up.sh)):

1. **Docker**: si no responde, abre Docker Desktop y espera hasta ~2.5 minutos.
2. **API**: crea `apps/api/.dev.vars` (el `SERVICE_TOKEN` local), aplica las migraciones a la
   D1 local, carga el **seed** si la base está vacía y arranca
   `wrangler dev --env dev --local --ip 0.0.0.0 --port 8787` en segundo plano.
3. **Web**: `next dev` en el puerto 3000 con `NEXT_PUBLIC_ENV=local`; reenvía `/api/*` a la
   API local (igual que la Pages Function en la nube).
4. **Motor**: `docker compose up --build` de `redis`, `agents` y `engine`
   ([`docker-compose.yml`](../docker-compose.yml)) apuntando a
   `http://host.docker.internal:8787`, con `TM_DEMO_STRATEGY=1`.
5. Espera a que `GET /api/engine/status` diga **en línea** (latido de `agents` y `engine`) e
   imprime las URLs.

Al final verás algo así:

```
Listo. trading_maxxing corre en local.

  Web        http://localhost:3000   (entras como demo@trading-maxxing.local, admin)
  API        http://127.0.0.1:8787/api/health
  Motor      ejecución: sim   estrategia de demo: demo_pulse (pulso de 2 min en BTCUSDT)
```

Variantes: `make dev FORECAST=1` (también construye y levanta Kronos, ver §6) y
`make dev MOTOR=native` (motor sin Docker, ver §8). `make dev-status` dice qué está arriba.

### Qué trae el seed local

[`apps/api/seed/seed.sql`](../apps/api/seed/seed.sql) (solo se carga en la D1 local):

- Usuario `demo@trading-maxxing.local` (**admin**), con perfil de riesgo 3 y 10,000 USDT, una
  meta de 12 % a un año, un backtest terminado e historial de operaciones de ejemplo.
- El bot **"Comité BTC/ETH"**, **detenido** (lo arrancas tú).
- Estrategias: `trend` (active), `kronos` (shadow), `intraday_momentum` (lab) y
  **`demo_pulse` (active)**.

### La estrategia de demo `demo_pulse` (solo para ver el flujo)

Las estrategias reales operan con velas de 4 h y diarias: en una demo de media hora no darían
ninguna señal. Por eso existe `demo_pulse`
([`tm_agents/strategies/demo_pulse.py`](../services/agents/src/tm_agents/strategies/demo_pulse.py)):

- Alterna **long / flat en BTCUSDT cada `TM_DEMO_PULSE_MINUTES` minutos** (2 por omisión)
  sobre velas de 1 minuto: compra, a los 2 minutos vende, a los 2 compra otra vez…
- Pasa por **el mismo camino** que cualquier estrategia: capa compartida (señal +
  SimulationGate) → `tm:signals` → BotRunner (allocator, exposición del nivel y RiskAgent) →
  `tm:decisions` → engine → `POST /internal/trades` → D1 → dashboard.
- Para que la SimulationGate la deje pasar le da un pronóstico **sintético y optimista** (como
  si fuera Kronos). Su `lab_report` también es inventado. **No es una estrategia de trading.**
- Solo tiene código cuando el worker corre con `TM_DEMO_STRATEGY=1` (lo pone `make dev`), y su
  fila solo existe en el seed local: **nunca está en dev ni en prod** (lo verifica
  `services/agents/tests/test_demo_pulse.py`). En la demo el tope de operaciones por día del
  RiskAgent sube a 10,000 (`TM_DEMO_MAX_TRADES_PER_DAY`) para que no se frene.
- Después de unas 30 operaciones cerradas, el LifecycleMonitor puede pasarla a **probation**
  (su Sharpe reciente es negativo por las comisiones): es el ciclo de vida funcionando. Sigue
  operando con la mitad del peso. `make dev-reset` la regresa a active.

### Ejecución simulada (sin llaves de Binance)

Sin `BINANCE_TESTNET_API_KEY`/`SECRET`, el engine arranca con `ENGINE_EXECUTION=auto` en modo
**`sim`** ([`tm_engine/sim.py`](../services/engine/src/tm_engine/sim.py)):

- Cada decisión se llena completa al **precio actual de la REST pública de Binance**
  (`api.binance.com`, sin llave; si la red lo bloquea usa `data-api.binance.vision` y, si
  tampoco, el último precio conocido o el cierre de la vela de la decisión).
- Comisión del laboratorio: **0.1 % por lado** (`ENGINE_SIM_FEE`).
- El SL/TP de cada compra se vigila con ese mismo precio.
- Los fills se registran por la API igual que en el modo real, con `rationale.execution = "sim"`.

El encabezado de la web y `make dev-status` muestran el modo (`sim` o `testnet`).

## 3. Qué ver en cada página

Abre **http://localhost:3000**. Entras directo como `demo@trading-maxxing.local` (en la nube
este paso es el login con código por correo de Cloudflare Access).

| Página | Qué ver |
|---|---|
| Encabezado (todas) | Tu correo, el badge **local** con el commit, y el indicador **Motor en línea** (latido de agents y engine en `/api/engine/status`; pasa a "desconectado" si apagas el motor). |
| **`/dashboard`** | Pulsa **Arrancar** en el bot. El estado pasa a **Operando** y en 2 a 4 minutos aparece la primera **Compra BTC/USDT** de **Demo (pulso)** en *Últimas operaciones*; 2 minutos después, la venta (la fila se cierra con su P&L). Abre **¿Por qué?** en una operación: señal, simulación (SimulationGate), riesgo y peso del sleeve. *Posiciones por estrategia* muestra el sleeve `demo_pulse`; la curva de **Equity** recibe una foto por minuto (`EQUITY_SNAPSHOT_SECONDS=60`). Todo se refresca solo (polling). **Detener** deja de abrir operaciones; **Paro de emergencia** además cierra lo abierto. |
| `/onboarding` | El asistente de perfil (capital, nivel de riesgo y metas). El usuario demo ya tiene perfil: puedes cambiarlo y guardar. |
| `/goals` | Probabilidad de cumplir la meta con el Monte Carlo y el abanico p10/p50/p90. |
| `/strategies` | El ciclo de vida de cada estrategia (research → lab → shadow → active → probation → retired), sus reportes del laboratorio y la equity de referencia (la de `demo_pulse` se llena sola mientras corre). Como admin puedes promover o retirar. |
| `/backtests` | Pide un backtest: queda `queued`, el engine lo toma (`GET /internal/backtests`) y al terminar ves el reporte. |
| `/admin` | Usuarios y los interruptores de noticias y explicaciones con LLM (§5). |

Por debajo puedes mirar:

```bash
make dev-status                                   # API, web, contenedores y latido
make dev-logs                                     # logs del motor en vivo (JSON por línea)
tail -f .dev/logs/api.log                         # cada petición a la API
curl -s localhost:8787/api/engine/status | jq     # el latido que ve la web
curl -s 'localhost:8787/api/trades?limit=5' | jq  # las operaciones (como el usuario demo)
docker compose exec redis redis-cli XLEN tm:decisions
```

## 4. Operar de verdad en Binance Spot Testnet

1. Entra a https://testnet.binance.vision, inicia sesión con GitHub y genera una llave
   (**HMAC**). Te da `API Key` y `Secret Key` con saldo de prueba (sin dinero real).
2. Copia el ejemplo y pon las llaves:

   ```bash
   cp .env.example .env
   # en .env:
   BINANCE_TESTNET_API_KEY=...
   BINANCE_TESTNET_API_SECRET=...
   ```

3. `make dev-down && make dev`. Con llaves, `ENGINE_EXECUTION=auto` usa el `TradingNode` de
   NautilusTrader contra Testnet: el motor imprime `Connected to Binance Testnet` en
   `make dev-logs` y el modo pasa a `testnet`. Las órdenes y los fills los ves en
   https://testnet.binance.vision y en el dashboard (sin `execution: "sim"`).

Para forzar un modo: `ENGINE_EXECUTION=sim|testnet|offline` en `.env` (`offline` no ejecuta
nada). Ojo: Binance bloquea IPs de EE. UU. (responde 451); desde México funciona.

## 5. Prender el LLM (opcional)

Por omisión todo corre **sin LLM**: las explicaciones de cada operación salen de una plantilla
y no hay filtro de noticias. Para probarlo:

1. Elige un proveedor gratuito y pon sus llaves en `.env`:

   ```bash
   LLM_PROVIDER=groq            # o workers-ai (CF_ACCOUNT_ID + CF_AI_API_TOKEN) u openai-compatible
   GROQ_API_KEY=...
   ```

   (Con Ollama en la Mac: `LLM_PROVIDER=openai-compatible`,
   `OPENAI_COMPAT_BASE_URL=http://host.docker.internal:11434/v1`, `OPENAI_COMPAT_MODEL=llama3.1`.)
2. `make dev-down && make dev`.
3. En **`/admin`** prende **Explicaciones** (y **Noticias** si quieres el veto por noticias de
   RSS). El motor lee el interruptor de `/internal/settings` (hasta 5 minutos; reinicia
   `agents` para verlo ya: `docker compose restart agents`).
4. Las explicaciones aparecen en las decisiones (`GET /api/decisions`, `kind = "explanation"`).

Tabla completa de variables: [`services/agents/README.md`](../services/agents/README.md).

## 6. Kronos (opcional, pesado)

Kronos-small (PyTorch) no está activo en el seed local (está en shadow y opera con velas de
4 h), así que el arranque normal **no construye** su imagen. Para levantarlo:

```bash
make dev FORECAST=1          # perfil "forecast" de docker compose, en CPU
# o nativo con Metal (más rápido en Apple Silicon), en otra terminal:
make forecast-mps            # http://localhost:8001; con FORECAST_URL=http://host.docker.internal:8001 en .env
```

La primera llamada descarga el modelo (~110 MB).

## 7. Prueba automática del flujo completo: `make e2e-local`

```bash
make e2e-local
```

Corre [`apps/web/tests/e2e/local-stack.spec.ts`](../apps/web/tests/e2e/local-stack.spec.ts)
con Playwright contra el stack real (sin mocks). Si el stack no estaba arriba lo levanta con
`make dev` y al final lo apaga (`KEEP_UP=1 make e2e-local` lo deja corriendo). La prueba:

1. revisa `/api/health` por el proxy de la web;
2. abre el sitio y hace el onboarding si el usuario no tiene perfil;
3. en el dashboard crea el bot si no hay y pulsa **Arrancar** (queda **Operando**);
4. espera hasta **6 minutos** a que el motor registre al menos una operación nueva de
   `demo_pulse` (`GET /api/trades`);
5. verifica que el dashboard la muestre sin recargar (Demo (pulso), BTC/USDT);
6. verifica que `/api/engine/status` diga en línea con `agents` y `engine`.

Tarda de 2 a 5 minutos. El reporte queda en `apps/web/playwright-report/`.

## 8. Apagar, reiniciar y problemas comunes

```bash
make dev-down     # apaga API, web y motor (conserva la D1 local y Redis)
make dev-reset    # además borra la D1 local y Redis: el siguiente make dev recarga el seed
```

| Síntoma | Qué hacer |
|---|---|
| `Docker no arrancó` | Abre Docker Desktop a mano y vuelve a correr `make dev`. Sin Docker: `brew install redis` y `make dev MOTOR=native` (corre agents y engine con `uv` en la Mac; logs en `.dev/logs/`). |
| `el puerto 8787/3000 está ocupado` | Otro `wrangler dev` o `next dev` quedó vivo: `make dev-down`, o `lsof -i :3000` y termínalo. |
| El indicador dice **Motor desconectado** | `make dev-logs`. Si ves `401`, el `TM_SERVICE_TOKEN` no coincide con `SERVICE_TOKEN` de `apps/api/.dev.vars`. Si ves `Connection refused` a `host.docker.internal:8787`, la API no está escuchando en `0.0.0.0` (usa `make dev`, no `pnpm dev`). |
| Arranqué el bot y no hay operaciones | Espera 4 minutos (una fase completa del pulso). Revisa en `make dev-logs` que `agents` diga `bot … arrancado` y que no haya `no entra en …` (el RiskAgent o la gate explican por qué). Las velas vienen de `api.binance.com`: sin internet no hay señales. |
| `pérdida del día` o `kill switch` en los logs | El bot llegó a su límite de pérdida diaria o de drawdown (comisiones de muchas operaciones). `make dev-reset`. |
| La web tarda la primera vez | `next dev` compila cada página la primera vez; `make dev` ya las pide antes. |
| Cambié código del motor | `make dev` reconstruye las imágenes (`--build`); basta con correrlo otra vez. |
| Quiero ver la base | `cd apps/api && pnpm exec wrangler d1 execute DB --env dev --local --command "SELECT * FROM trades ORDER BY opened_at DESC LIMIT 5"` |

Piezas sueltas: `make api` (solo la API), `make web` (solo la web) y `make motor-local` (solo el
motor). Más detalle del motor en la Mac con las imágenes de Docker Hub:
[`arquitectura/motor-en-la-mac.md`](arquitectura/motor-en-la-mac.md).
