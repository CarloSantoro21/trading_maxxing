# engine

Motor de ejecución y backtest sobre [NautilusTrader](https://nautilustrader.io) `1.231.0`
(versión exacta en `pyproject.toml`).

```bash
uv run --package tm-engine python -m tm_engine          # en bucle, con heartbeat
uv run --package tm-engine python -m tm_engine --once   # imprime su estado y termina
```

## Piezas

| Módulo | Qué hace |
| --- | --- |
| `strategy.py` | `AgenticStrategy` (subclase de `Strategy`): recibe `TradeDecision` y manda las órdenes. |
| `service.py` | `TradingEngineService`: `start(profile)`, `stop()`, `submit(decision)`, `get_positions()`. |
| `backtest.py` | `build_engine` / `run_backtest`: `BacktestEngine` con un venue simulado de Binance Spot. |
| `decisions.py` | Sobre `DecisionEnvelope` y fuentes de decisiones (Redis, programada para backtest). |
| `sleeves.py` | `SleeveLedger`: posición, costo promedio y P&L por estrategia (sleeve). |
| `recorder.py` | Registro de fills: en memoria o en la tabla `trades` de Supabase. |
| `instruments.py` | `CurrencyPair` de BTCUSDT y ETHUSDT y conversión de velas de `tm_marketdata`. |

## Órdenes (spot)

- **Compra** → orden *bracket*: entrada `MARKET` (OTO) y, cuando se llena, un stop-loss
  `STOP_MARKET` y un take-profit `LIMIT` ligados como OUO (el que se llena cancela al otro,
  equivalente a un OCO). Los dos hijos son `reduce_only`.
- **Venta** → en spot significa quedarse en USDT (*flat*): se cancelan el SL y el TP del sleeve
  y se vende a mercado, `reduce_only`, toda la cantidad que tiene ese sleeve. Nunca se abren
  cortos: una venta sin posición se rechaza.
- Se rechazan también: decisiones con `rationale.risk.approved = false`, una segunda compra
  del mismo sleeve con posición abierta, símbolos sin instrumento y compras sin saldo libre.
- El adaptador de Binance de Nautilus no manda listas de órdenes ligadas (OCO) a Binance Spot,
  así que en vivo el SL y el TP se **emulan** localmente (`emulation_trigger="BID_ASK"`) y la
  estrategia corre con `manage_contingent_orders=True`. En el backtest el motor de matching
  simulado soporta OTO/OUO de forma nativa.

## Sleeves

Cada orden lleva los tags `sleeve:<nombre>` y `role:<entry|sl|tp|exit>`. Nautilus junta las
posiciones por instrumento (OMS *netting*), así que `SleeveLedger` lleva aparte la cantidad, el
costo promedio, las comisiones y el P&L realizado de cada sleeve a partir de los fills.
`get_positions()` devuelve los dos niveles: `{"sleeves": {...}, "venue": [...]}`.

## Decisiones por Redis (`tm:decisions`)

El orquestador publica cada decisión en el **stream** `tm:decisions` con un solo campo `data`
que contiene un `DecisionEnvelope` en JSON:

```text
XADD tm:decisions * data '{"id": "d-123", "sleeve": "trend", "decision": { ...TradeDecision... }}'
```

| Campo | Tipo | Descripción |
| --- | --- | --- |
| `id` | `str \| null` | Identificador opcional para trazabilidad. |
| `sleeve` | `str` (`[a-z0-9_-]`, ≤ 64) | Estrategia dueña del capital. Si falta, se usa el `source` de la primera señal del `rationale`, o `default`. |
| `decision` | objeto | `TradeDecision` de `tm_shared` serializado con `model_dump(mode="json")`. |

Desde Python: `tm_engine.decisions.publish_decision(redis_client, envelope)`. El motor lee
desde el último id existente al arrancar (no reprocesa decisiones viejas), descarta con un
aviso en el log las entradas malformadas y revisa el stream cada `poll_interval_secs` (1 s) y
en cada vela. Si Redis no está disponible sigue funcionando con `submit()`.

## Modo TESTNET y modo sin conexión

`TradingEngineService` arma un `TradingNode` con el adaptador de Binance Spot en
`BinanceEnvironment.TESTNET` (datos y ejecución), con las llaves de:

| Variable | Uso |
| --- | --- |
| `BINANCE_TESTNET_API_KEY`, `BINANCE_TESTNET_API_SECRET` | Llaves de https://testnet.binance.vision. Si falta alguna, el motor arranca en modo `offline` (sin conexión). |
| `REDIS_URL` | Stream de decisiones. |
| `ENGINE_SYMBOLS` (`BTCUSDT`), `ENGINE_TIMEFRAME` (`1m`), `ENGINE_PROFILE` (`default`) | Perfil de ejecución. |
| `ENGINE_BOT_ID` + `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` | Si están, cada operación se guarda en `public.trades` (con `rationale.sleeve`). |
| `HEARTBEAT_FILE` (`/tmp/heartbeat`), `HEARTBEAT_INTERVAL` (`10`) | Heartbeat del contenedor. |

El heartbeat es un JSON con el estado:
`{"ts": ..., "mode": "testnet" | "offline", "connected": false, "error": "faltan ...", ...}`.
Los logs de Nautilus salen por stdout (sin colores) para que los recoja Docker/CloudWatch.

## Backtest

```python
from tm_engine.backtest import run_backtest
from tm_engine.decisions import DecisionEnvelope, ScheduledDecisionSource

frame = MarketDataService().get_bars("BTCUSDT", "1m", start, end)
result = run_backtest({"BTCUSDT": frame}, ScheduledDecisionSource(envelopes))
result.sleeves["trend"]["realized_pnl"], result.fills, result.final_balance_usdt
```

Venue `BINANCE` simulado: cuenta `CASH` en USDT (100 000 por defecto), OMS netting, comisión
de 0.1 % por lado y ejecución sobre OHLC de las velas. Las velas se fechan a su **cierre**
(`open_time` + duración) para no usar información futura. `ScheduledDecisionSource` suelta
cada decisión cuando el reloj del motor alcanza su `decided_at`.

Las pruebas (`tests/test_backtest_scenarios.py`) cubren con velas sintéticas: estructura del
bracket, take-profit, stop-loss, venta con `reduce_only`, rechazo por riesgo, decisiones
inválidas, saldo insuficiente y P&L atribuido por sleeve.
