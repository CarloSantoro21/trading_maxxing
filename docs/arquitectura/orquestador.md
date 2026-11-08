# AgentOrchestrator: capa compartida y un bot por usuario (#19)

El orquestador sigue el diseño de la sección 3.4 de la propuesta: lo que es **de la app** se
calcula una vez para todos y lo que es **de cada usuario** lo calcula su bot.

```
                 ┌──────────────── capa compartida (una vez) ────────────────┐
 velas cerradas ─▶ estrategia viva → Signal → veto de noticias → SimulationGate │──▶ tm:signals
 (MarketData)    └────────────────────────────────────────────────────────────┘
                                                                     │
                 ┌──────────── capa por usuario (un BotRunner por bot) ───────┐
 tm:commands ───▶│ peso del sleeve × exposición del nivel × confianza          │──▶ tm:decisions
 (API, #21)      │ → RiskAgent con los límites del nivel                       │──▶ tm:shadow
                 └────────────────────────────────────────────────────────────┘
                                                                     │
                                         engine (AgenticStrategy) ◀──┘  órdenes por bot y sleeve
```

Código: `services/agents/src/tm_agents/orchestrator/`.

| Módulo | Qué hace |
|---|---|
| `shared.py` | `SharedSignalLayer`: estrategias `active`, `probation` y `shadow` del registro; señal, veto y gate por estrategia y símbolo |
| `events.py` | `SignalEvent` (formato de `tm:signals`) y `StrategySnapshot` (lo que el bot necesita del `lab_report`) |
| `policy.py` | Niveles de cada operación por estrategia: stop de catástrofe (salida por señal) o stop real (Kronos), y horizonte del gate |
| `exposure.py` | Exposición de cada sleeve por nivel de riesgo, con la curva de sensibilidad del laboratorio |
| `bot.py` | `BotRunner`: posición objetivo, compras y ventas parciales, libro virtual, comandos |
| `supervisor.py` | `BotSupervisor`: un runner por bot en `running` (tabla `bots`), comandos de `tm:commands` |
| `streams.py` | Lectura y escritura de los streams de Redis y sus versiones en memoria |
| `runtime.py` | `OrchestratorWorker` (en vivo) y `run_offline` (backtest) |

## Capa compartida

En cada cierre de vela (el worker revisa cada minuto, con 5 s de margen para que Binance ya
sirva la vela), para cada estrategia viva cuyo timeframe cerró y para cada símbolo:

1. Arma el `AgentContext` con las velas **cerradas** hasta ese instante (sin look-ahead).
2. Pide la `Signal`. Kronos se llama **una vez por símbolo**: su pronóstico se guarda en el
   agente y se reutiliza para la señal y para las trayectorias de la SimulationGate. No
   depende de cuántos bots haya.
3. Consulta el veto vigente del símbolo (global, luego el activo base y luego la moneda de
   cotización), leído de Redis igual que el `NewsAgent`. Si la consulta falla, no veta
   (fail-open).
4. Si la señal es `long`, corre la SimulationGate (bootstrap histórico con comisión de 0.1 %,
   combinado con las trayectorias de Kronos cuando aplica).
5. Publica un `SignalEvent` en `tm:signals`.

Una estrategia que falla (sin código, error al construirla o al analizar) se registra y no
detiene a las demás.

### Formato de `tm:signals`

Un solo campo `data` con el JSON del evento (igual que `tm:decisions`):

```
XADD tm:signals MAXLEN ~ 10000 * data '<SignalEvent en JSON>'
```

```jsonc
{
  "id": "trend:BTCUSDT:2024-03-01T00:00:00Z",   // estrategia:símbolo:cierre (idempotencia)
  "strategy": "trend",
  "state": "active",                            // active | probation | shadow
  "symbol": "BTCUSDT",
  "bar_close": "2024-03-01T00:00:00Z",
  "price": "61234.5",                           // último cierre
  "signal": { /* Signal de tm_shared */ },
  "veto": null,                                 // o {scope, impact, reason, expires_at}
  "sim": { /* SimResult */ },                   // null si la señal no es long
  "sim_error": null,                            // por qué no hubo simulación
  "stop_loss": "3061.72", "take_profit": "489876.0",
  "hard_stop": false,                           // true = stop real (Kronos)
  "catalog": [ /* StrategySnapshot de todas las estrategias vivas */ ]
}
```

`catalog` viaja en cada evento para que el bot calcule sus pesos sin volver a leer el
registro: el evento basta para reproducir la decisión.

## Capa por usuario

`BotSupervisor.sync()` concilia cada minuto con la tabla `bots` (los que están en `running`,
con su `profile` embebido). Cada bot tiene su `BotRunner`, que por cada evento:

1. **Peso del sleeve**: `PortfolioAllocator` (pesos globales) sobre el `catalog`: inversa de la
   volatilidad validada, correlación, horizonte del perfil y estado (`probation` recibe la
   mitad). El capital del sleeve en un símbolo es `equity × peso / número de instrumentos`
   (el laboratorio reparte cada sleeve por igual entre BTC y ETH).
2. **Exposición del nivel**: ver la sección siguiente.
3. **Posición objetivo** = capital del sleeve × exposición × confianza (0 si la señal no es
   `long`). En el TrendAgent la confianza es la fracción de canales Donchian en largo, así que
   cada canal mueve 1/4 del sleeve.
4. Si la fracción objetivo sube, compra la diferencia: pasa por el veto, la SimulationGate
   (sin resultado o con valor esperado ≤ 0 no entra) y `RiskAgent.evaluate_target` con los
   límites del nivel (exposición máxima, pérdida del día, operaciones del día, kill switch).
   Si baja, vende la parte proporcional de lo que tiene; si llega a 0, cierra todo. Reducir
   riesgo nunca se bloquea. Si no cambia, no opera: no hay rebalanceos por el precio.
5. Publica un `DecisionEnvelope` en `tm:decisions` con `bot_id`, `sleeve` y `target_quantity`.

Las ventas parciales que quedarían bajo el mínimo de Binance (10 USDT) no se hacen; si el
resto de la posición quedaría bajo el mínimo, se cierra todo.

### Exposición por nivel

La regla original (`min(1, drawdown_del_nivel / drawdown_p95)`) supone que el drawdown baja en
proporción a lo invertido. El laboratorio mostró que no
([resultados-trend.md](../estrategias/resultados-trend.md), "Sensibilidad: exposición
reducida"): con 33 % invertido el p95 del TrendAgent es de 25.2 %, no de 20 %.

- **Con curva de sensibilidad** (pares `(exposición, p95)` del laboratorio): se interpola la
  exposición cuyo p95 relativo (`p95(k) / p95(1)`) iguala `drawdown_del_nivel / p95`. La curva
  del TrendAgent está en `exposure.TREND_SENSITIVITY`; un reporte nuevo puede traer la suya en
  `lab_report["exposure_sensitivity"]`.
- **Sin curva**: `min(1, drawdown_del_nivel / (p95 × 1.3))`. El 1.3 es la pendiente medida del
  TrendAgent en exposiciones bajas (19.6 / 61.5 / 0.25 ≈ 1.27), así que nunca invierte más que
  la curva medida.
- Sin p95 (sin Monte Carlo), la exposición es 0.

Con el p95 de 61.5 % del TrendAgent:

| Nivel | Drawdown tolerado | Regla proporcional | Con la curva |
|---|---|---|---|
| 1 | 10 % | 16.3 % | 12.8 % |
| 2 | 15 % | 24.4 % | 19.1 % |
| 3 | 20 % | 32.5 % | 25.6 % |
| 4 | 30 % | 48.8 % | 40.4 % |
| 5 | 40 % | 65.0 % | 56.6 % |

No hay topes manuales del administrador: todo sale del perfil, del laboratorio y del estado.

### Shadow

Las estrategias en `shadow` hacen todo el ciclo con el peso que tendrían si fueran activas,
sobre un libro virtual aparte, y se publican en `tm:shadow` con `shadow=true`. Nunca llegan a
`tm:decisions`, y el motor rechaza cualquier sobre con `shadow=true` por si acaso.

### Comandos (`tm:commands`)

Los publica la API (#21) con campos planos `type`, `bot_id`, `user_id`, `reason`, `issued_at`
y `payload`.

| Comando | Efecto |
|---|---|
| `start` | Arranca o reanuda el runner del bot |
| `stop` | Deja de operar; las posiciones se quedan |
| `kill` | Vende a mercado todas las posiciones del bot (`target_quantity = 0`) y lo detiene |

El kill switch por drawdown del RiskAgent hace lo mismo que `kill`.

## Engine

`AgenticStrategy` (services/engine) ahora lleva un libro por `bot/sleeve` y, cuando el sobre
trae `target_quantity`:

- una compra con el sleeve abierto agrega un **tramo** (otro bracket con su SL y TP);
- una venta con objetivo mayor que 0 es **parcial**: vende `decision.quantity` y achica la
  protección de los tramos del más reciente al más viejo;
- una venta con objetivo 0, o que cubre toda la posición, cierra completo (como en el #69).

Sin `target_quantity` se conserva el comportamiento anterior.

## Backtest y en vivo

Las mismas clases corren en los dos modos; solo cambian la fuente de velas y los streams:

| | En vivo | Backtest |
|---|---|---|
| Velas | `MarketDataBarsProvider` | `FrameBarsProvider` (DataFrames) |
| Estrategias | `StrategyRegistry` con `PostgRESTBackend` | `StrategyRegistry` con `InMemoryBackend` |
| `tm:signals` | `RedisStreamSink` / `RedisSignalSource` | `run_offline` (en memoria) o `ListSignalSource` |
| `tm:decisions` | `RedisStreamSink` | `MemorySink` → `ScheduledDecisionSource` del engine |

```python
result = run_offline(shared_layer, [bot_a, bot_b], start, end)
backtest = run_backtest(frames, ScheduledDecisionSource(result.decisions), timeframe=Timeframe.D1)
```

`services/engine/tests/test_orchestrator_backtest.py` lo hace de punta a punta y comprueba que
la posición de cada bot en el motor coincide con su libro virtual.

## Despliegue

El servicio `agents` de `docker-compose.yml` corre las dos capas en el mismo proceso
(`python -m tm_agents`), comunicadas por Redis igual que si estuvieran separadas. Con
`ORCHESTRATOR_SHARED=0` o `ORCHESTRATOR_BOTS=0` se apaga una de las dos para correrlas en
contenedores distintos. Variables: `REDIS_URL`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`,
`TM_DATA_DIR`, `ORCHESTRATOR_SYMBOLS` y `FORECAST_URL` (Kronos).

## Limitaciones conocidas

- **El libro del bot es virtual y vive en memoria.** Supone que cada decisión se llena al precio
  del evento con 0.1 % de comisión. Si el worker se reinicia, los bots empiezan sin posiciones
  aunque el motor tenga posiciones abiertas; falta reconstruir el libro desde la tabla `trades`
  o desde los fills del motor.
- **Salidas por SL/TP real (Kronos).** Las ejecuta el motor; el libro virtual las refleja
  cuando el precio de un evento posterior del símbolo cruza el nivel (cierres de vela, no
  máximos y mínimos intravela).
- **Vetos en backtest.** El veto se lee del almacén con el reloj real; para un backtest con
  noticias hay que inyectar un `VetoProvider` con el histórico.
