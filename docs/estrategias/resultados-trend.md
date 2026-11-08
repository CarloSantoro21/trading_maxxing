# Resultados del TrendAgent: walk-forward 2021–2024

Backtest formal del [TrendAgent](../../services/agents/src/tm_agents/strategies/trend.py) (conjunto de canales Donchian en velas diarias) con el laboratorio del #13. Issue: #31.

> **Veredicto: RECHAZADO** con el techo global de drawdown de 40 % (el p95 se mide a tamaño completo y cada usuario invierte después la fracción que cabe en su nivel). Pasa el Sharpe out-of-sample (0.98 > 0.5) y la evidencia (164 trades en 4 años), pero el drawdown p95 del Monte Carlo es de 61.5 % sobre el horizonte de 4 años, y de 45.2 % en horizontes de un año: rebasa el techo con cualquiera de las dos lecturas. Le gana a comprar y mantener en Sharpe (0.98 contra 0.83) y con menos de la mitad del drawdown máximo (33.9 % contra 77.4 %), pero gana menos en retorno total (229 % contra 288 %).

## 1. Cómo se corrió

```bash
uv run --package tm-engine python -m tm_engine.lab run --strategy trend --symbols BTCUSDT ETHUSDT --data-dir data/bars --out-dir docs/estrategias/reportes
```

- **Walk-forward** (`tm_engine.lab.walk_forward`): 16 tramos. En cada uno se ajusta con 12 meses (in-sample) y se prueba en los 3 siguientes (out-of-sample), de 2021-T1 a 2024-T4. Los tramos out-of-sample se pegan en una sola corrida continua de 4 años (las posiciones pasan de un tramo al siguiente).
- **Datos**: velas de Binance Spot de BTCUSDT y ETHUSDT de 1 día, 2020-01-01 a 2024-12-31. **El holdout 2025 no se cargó** (la guarda de `WalkForwardConfig` lo impide sin `--holdout`).
- **Capital**: 100,000 USDT repartidos por igual entre BTC y ETH, y dentro de cada símbolo en cuatro sub-sleeves iguales, uno por periodo Donchian. La exposición total es el voto promedio del conjunto, que es la `confidence` de la señal del agente. En spot, `long` = comprar con todo el efectivo del sub-sleeve y `flat` = vender y quedarse en USDT; no hay cortos.
- **Costos**: comisión de 0.1 % por lado (Binance Spot), cobrada en USDT sobre el nocional. Las órdenes se llenan al cierre de la vela en la que se decide (sin look-ahead).
- **Ejecución**: el out-of-sample se ejecuta en **Nautilus** (`tm_engine.backtest.run_backtest`, venue simulado de Binance). La cuadrícula in-sample usa un simulador vectorizado equivalente (`tm_engine.lab.simulator`); el reporte compara los dos en la misma corrida y la diferencia del equity diario es < 1e-9 (ver la última sección).
- **Optimización in-sample**: los cuatro periodos (20/50/100/200 por defecto) se escalan por un multiplicador de 0.5, 0.75, 1 o 1.25 (4 combinaciones). Se elige la combinación con mayor Sharpe in-sample (diario, anualizado con 365 días).
- **Métricas**: sobre la **serie diaria de equity** del sleeve con las posiciones abiertas valuadas a mercado (00:00 UTC). Sharpe con 365 días y tasa libre de riesgo 0.
- **Monte Carlo**: `tm_agents.montecarlo.bootstrap` sobre los 1,461 retornos diarios out-of-sample (bloques de 20 días, 10,000 simulaciones, semilla 42) y `evaluate(..., oos_years=4)` con `ApprovalCriteria(max_drawdown_p95=0.40)`: Sharpe > 0.5, drawdown p95 a tamaño completo < 40 % (techo global de [criterios-de-aprobacion.md](criterios-de-aprobacion.md)) y ≥ 100 trades, o ≥ 30 con ≥ 3 años out-of-sample. El reporte también da la exposición por nivel de riesgo, `min(1, drawdown del nivel / p95)`.
- **Reporte completo** (por ventana, la cuadrícula in-sample y la serie diaria de equity en JSON): [`reportes/trend.json`](reportes/trend.json).

## 2. Resultados

### Agregado out-of-sample contra comprar y mantener

| | Estrategia | Comprar y mantener |
|---|---|---|
| Periodo | 2021-01-01 a 2025-01-01 | igual |
| Retorno total | 228.8 % | 288.1 % |
| CAGR | 34.6 % | 40.3 % |
| Sharpe (365 d) | 0.98 | 0.83 |
| Volatilidad anual | 37.8 % | 71.7 % |
| Drawdown máximo | 33.9 % | 77.4 % |
| Equity final (de 100,000 USDT) | 328,817 | 388,079 |
| Trades | 164 (41 al año) | 2 |
| Duración mediana de un trade | 16.0 días | — |
| Trades ganadores | 48.8 % | — |
| Comisiones pagadas | 10,952 USDT (11.0 % del capital) | — |
| Tiempo con posición | 38.1 % | 100 % |
| Años out-of-sample | 4.00 | — |

![Equity diario out-of-sample contra comprar y mantener](img/trend-walk-forward.png)

### Por símbolo

| Símbolo | Retorno | Sharpe | Drawdown | Trades | Comisiones | Retorno B&H | Sharpe B&H | Drawdown B&H |
|---|---|---|---|---|---|---|---|---|
| BTCUSDT | 160.6 % | 0.88 | 39.2 % | 82 | 3,137 | 218.7 % | 0.78 | 76.6 % |
| ETHUSDT | 297.0 % | 0.99 | 40.3 % | 82 | 7,815 | 357.5 % | 0.87 | 79.3 % |

### Por ventana (walk-forward)

| Tramo OOS | In-sample | Parámetros elegidos | Sharpe IS | Retorno OOS | Sharpe OOS | Drawdown OOS | Trades | Comisiones | Retorno B&H |
|---|---|---|---|---|---|---|---|---|---|
| 2021-T1 | 2020-01 a 2020-12 | 10/25/50/100 | 2.34 | 87.2 % | 3.49 | 20.2 % | 15 | 427 | 131.6 % |
| 2021-T2 | 2020-04 a 2021-03 | 20/50/100/200 | 2.86 | 4.9 % | 0.63 | 32.9 % | 5 | 331 | -6.9 % |
| 2021-T3 | 2020-07 a 2021-06 | 10/25/50/100 | 2.67 | 9.3 % | 1.02 | 18.5 % | 12 | 639 | 30.0 % |
| 2021-T4 | 2020-10 a 2021-09 | 10/25/50/100 | 2.55 | 3.8 % | 0.60 | 15.7 % | 12 | 730 | 18.0 % |
| 2022-T1 | 2021-01 a 2021-12 | 10/25/50/100 | 1.74 | 1.3 % | 0.48 | 6.5 % | 10 | 443 | -8.5 % |
| 2022-T2 | 2021-04 a 2022-03 | 10/25/50/100 | 0.80 | -6.4 % | -1.63 | 10.7 % | 1 | 210 | -64.5 % |
| 2022-T3 | 2021-07 a 2022-06 | 10/25/50/100 | 0.41 | 4.3 % | 0.68 | 12.6 % | 8 | 508 | 15.6 % |
| 2022-T4 | 2021-10 a 2022-09 | 10/25/50/100 | -0.02 | -4.7 % | -0.82 | 12.5 % | 10 | 720 | -11.3 % |
| 2023-T1 | 2022-01 a 2022-12 | 10/25/50/100 | -0.67 | 19.1 % | 2.41 | 5.7 % | 17 | 805 | 57.3 % |
| 2023-T2 | 2022-04 a 2023-03 | 10/25/50/100 | 0.15 | -5.1 % | -0.78 | 14.9 % | 11 | 1,078 | 6.4 % |
| 2023-T3 | 2022-07 a 2023-06 | 10/25/50/100 | 0.40 | -6.9 % | -3.98 | 7.7 % | 5 | 556 | -13.0 % |
| 2023-T4 | 2022-10 a 2023-09 | 10/25/50/100 | 0.26 | 16.7 % | 2.06 | 6.7 % | 16 | 1,053 | 42.4 % |
| 2024-T1 | 2023-01 a 2023-12 | 10/25/50/100 | 1.25 | 31.2 % | 2.82 | 17.2 % | 13 | 918 | 62.5 % |
| 2024-T2 | 2023-04 a 2024-03 | 20/50/100/200 | 1.64 | -12.0 % | -2.37 | 13.1 % | 6 | 663 | -7.7 % |
| 2024-T3 | 2023-07 a 2024-06 | 10/25/50/100 | 1.35 | -0.4 % | -0.14 | 3.7 % | 9 | 573 | -16.4 % |
| 2024-T4 | 2023-10 a 2024-09 | 10/25/50/100 | 1.49 | 10.8 % | 1.32 | 9.5 % | 14 | 1,298 | 35.6 % |

La estrategia le ganó en retorno a comprar y mantener en 6 de 16 tramos.

### Estabilidad de los parámetros

- 2 combinaciones distintas en 16 tramos y 4 cambios entre tramos consecutivos.
- La más frecuente (`10/25/50/100`) se eligió en 88 % de los tramos.

| Parámetros | Tramos |
|---|---|
| 10/25/50/100 | 14 |
| 20/50/100/200 | 2 |

### Monte Carlo del drawdown

Block bootstrap de los 1461 retornos diarios out-of-sample del sleeve (bloques de 20 días, 10,000 simulaciones, semilla 42). Cada simulación cubre el mismo horizonte que el backtest.

| Drawdown máximo | p50 | p95 | p99 |
|---|---|---|---|
| Simulado | 39.8 % | 61.5 % | 71.6 % |

Probabilidad de ruina (perder la mitad del capital en algún punto): 3.5 %.

Como referencia, con el mismo bootstrap pero en horizontes de un año (el criterio habla de "un año malo"): p50 23.7 %, p95 45.2 % y p99 54.5 %. El veredicto usa el horizonte completo, que es más exigente.

### Criterios de aprobación

| Criterio | Valor | Umbral | ¿Pasa? |
|---|---|---|---|
| Sharpe out-of-sample | 0.98 | > 0.50 | sí |
| Drawdown p95 | 61.5 % | < 40 % | no |
| Evidencia (trades) | 164 en 4.0 años | ≥ 100, o ≥ 30 con ≥ 3 años | sí |

Exposición por nivel de riesgo si se aprobara, `min(1, drawdown del nivel / p95)`: nivel 1: 16 %, nivel 2: 24 %, nivel 3: 33 %, nivel 4: 49 %, nivel 5: 65 %.

Sharpe contra comprar y mantener (propuesta pendiente de aprobación, no entra al veredicto): 0.98 contra 0.83, le gana.

### Ejecución: Nautilus contra el simulador vectorizado

- Nautilus (`run_backtest`, comisión de 0.1 % por lado): equity final de 328,817 USDT con 326 fills en 0.2 s.
- Simulador vectorizado: 328,817 USDT. Diferencia relativa máxima del equity diario: 0.000 %.
- Decisiones rechazadas por el motor: 0.

## 3. Lectura

1. **El edge es de riesgo, no de retorno.** La estrategia sigue las subidas de 2021 y de 2024 y se sale casi por completo de la caída de 2022 (el drawdown de comprar y mantener llegó a 77 %; el de la estrategia, a 34 %). Por eso le gana en Sharpe con la mitad de la volatilidad (38 % contra 72 %), aunque termina con menos dinero: está en el mercado solo 38 % del tiempo y se pierde parte de los rebotes.
2. **El drawdown sigue siendo el de cripto.** Aunque es la mitad que el de comprar y mantener, una caída de 34 % en el backtest y de 61.5 % en el p95 del Monte Carlo rebasa el techo de 40 %. El Monte Carlo es más severo que el backtest porque revuelve bloques de 20 días y puede encadenar los meses malos de 2021 y 2022.
3. **Los parámetros son estables.** El conjunto más rápido (10/25/50/100) se eligió en 14 de 16 tramos. El rendimiento no viene de reoptimizar cada trimestre.
4. **Las comisiones importan, pero no deciden.** 164 trades (41 al año, duración mediana de 16 días) pagaron 10,952 USDT, 11 % del capital inicial en 4 años.
5. **Le gana a comprar y mantener en retorno solo en 6 de 16 tramos**: en los trimestres alcistas fuertes (2021-T1, 2023-T1, 2024-T1) entra tarde y gana menos. Su ventaja está en los tramos bajistas (2022-T2: −6 % contra −65 %).

### Sensibilidad: exposición reducida (no es parte del veredicto)

Si el sleeve operara solo una fracción `k` de su capital y dejara el resto en USDT, los retornos diarios se escalan por `k` (aproximación: ignora que la comisión también se escala). Con el mismo bootstrap:

| Exposición `k` | Retorno total | Drawdown máximo | Drawdown p95 (MC) | Sharpe |
|---|---|---|---|---|
| 100 % | 228.8 % | 33.9 % | 61.5 % | 0.98 |
| 65 % | 131.5 % | 23.0 % | 44.8 % | 0.98 |
| 60 % | 118.9 % | 21.4 % | 42.0 % | 0.98 |
| 50 % | 94.9 % | 18.0 % | 36.2 % | 0.98 |
| 33 % | 57.9 % | 12.2 % | 25.2 % | 0.98 |
| 25 % | 42.1 % | 9.3 % | 19.6 % | 0.98 |

Dos lecturas:

- Con la mitad del sleeve en riesgo (o menos) el p95 cae debajo del techo de 40 %. Una versión del agente que invierta como máximo ~50 % del sleeve (por ejemplo, con *volatility targeting*: el TrendAgent ya publica la volatilidad anualizada en `metadata`) probablemente pasaría los tres criterios. Esa versión no se corrió aquí; queda como siguiente paso y tendría que pasar por el walk-forward completo.
- La regla de exposición por usuario supone que el drawdown escala en proporción a lo invertido. Con este bootstrap el drawdown baja menos que en proporción al reducir la exposición: el nivel 3 (20 %) invertiría 20 / 61.5 = 33 % del sleeve y su p95 sería de 25.2 %, no de 20 %; para llegar a 20 % hace falta ~25 %. La diferencia viene de la capitalización compuesta y conviene tenerla en cuenta en el PortfolioAllocator.

## 4. Holdout 2025: pendiente

El holdout (2025-01-01 a 2025-12-31) **no se ha corrido**. Se corre una sola vez al final del desarrollo, con los parámetros que elija la última ventana:

```bash
uv run --package tm-engine python -m tm_engine.lab run --strategy trend --symbols BTCUSDT ETHUSDT --data-dir data/bars --out-dir docs/estrategias/reportes --holdout
```

Escribe `reportes/trend-holdout.json`, `.md` y `.png` y no sobrescribe este reporte.

## 5. Limitaciones

- Sin deslizamiento (*slippage*): las órdenes se llenan al cierre de la vela diaria. Con 41 trades al año y BTC/ETH, el efecto esperado es chico frente a la comisión.
- Sizing todo o nada por sub-sleeve; el dimensionamiento por volatilidad del RiskAgent no entra a este backtest.
- El techo de 40 % es el global de los criterios de aprobación; con `--max-drawdown` se puede reevaluar con otro límite.
