# Criterios de aprobación de estrategias

Antes de recibir capital, una estrategia tiene que pasar tres pruebas en el laboratorio:

| # | Criterio | Pregunta que responde |
|---|---|---|
| 1 | **Sharpe out-of-sample > 0.5** | ¿Gana de forma consistente, o solo con sustos enormes? |
| 2 | **Drawdown p95 < límite del perfil** | En un año malo pero realista, ¿cuánto cae? ¿Lo aguanta el usuario? |
| 3 | **Suficientes trades** | ¿El resultado es estadísticamente creíble o pudo ser suerte? |

Los tres se calculan **solo con datos que la estrategia no vio al ajustarse** (out-of-sample) e **incluyen comisiones**: 0.1 % por lado en Binance Spot.

---

## Conceptos previos

### In-sample y out-of-sample
- **In-sample**: los datos con los que se ajustan los parámetros de la estrategia. Ahí siempre se ve bien, porque se ajustó a esos datos.
- **Out-of-sample**: datos que nunca se usaron para ajustar. Es el examen real.

Una estrategia que solo funciona in-sample memorizó ruido. A eso se le llama **sobreajuste** (*overfitting*).

### Walk-forward: los "tramos"
En lugar de un solo corte, se repite el examen muchas veces:

| Ajusta con (12 meses) | Se prueba en (3 meses siguientes) |
|---|---|
| 2020-01 → 2020-12 | 2021 ene–mar |
| 2020-04 → 2021-03 | 2021 abr–jun |
| 2020-07 → 2021-06 | 2021 jul–sep |
| … | … |
| 2023-10 → 2024-09 | 2024 oct–dic |

Son **16 tramos de prueba**. Pegados forman **4 años (2021–2024) de resultados en examen**, que cruzan regímenes muy distintos:
- 2021: alcista
- 2022: bajista; BTC cayó ~77 % desde su máximo
- 2023: recuperación
- 2024: alcista

Al final, **2025 es el examen final** (*holdout*): se corre una sola vez y no se toca antes.

> Kronos tiene su propio calendario. Sus datos de preentrenamiento llegan a junio de 2024, así que no se evalúa antes. Solo se ajustan sus umbrales, con julio–diciembre de 2024, y 2025 es su holdout.

### Comprar y mantener
Cada reporte se compara contra **comprar BTC o ETH y no hacer nada** en el mismo periodo. Si una estrategia no le gana a eso en Sharpe, no vale la pena operarla.

---

## Criterio 1: Sharpe out-of-sample > 0.5

### Qué mide
**Cuánto ganas por cada unidad de "sustos".** Los sustos son la **volatilidad**: qué tanto sube y baja tu dinero en el camino.

```
Sharpe = retorno anual promedio ÷ volatilidad anual
```

(La fórmula formal resta la tasa libre de riesgo; aquí la tomamos como 0 por simplicidad.)

### Ejemplo
Dos estrategias que ganan **20 % al año**:

| | Camino | Volatilidad | Sharpe |
|---|---|---|---|
| **A** | Sube poquito casi todos los días | 10 % | 20 / 10 = **2.0** |
| **B** | +30 %, −25 %, +40 %, −20 %… | 40 % | 20 / 40 = **0.5** |

Terminan igual, pero con B pasas meses viendo pérdidas grandes. Además, es mucho más probable que en el siguiente año te toque la parte mala del camino.

### Qué NO significa
**Un Sharpe de 0.5 no significa "ganas 1.5×".** Significa que el retorno es la mitad de la volatilidad. Si la estrategia se mueve ±30 % al año, gana en promedio 15 %. Es una medida de **calidad** de la ganancia, no de **cantidad**.

### Cómo leerlo
| Sharpe | Lectura |
|---|---|
| ≤ 0 | No gana, o pierde |
| 0.5 | Modesto pero real |
| ≥ 1 | Muy bueno |
| ≥ 2 en un backtest | Sospechoso: probablemente sobreajuste o un error |

### Por qué 0.5
Con comisiones incluidas y medido solo out-of-sample, un Sharpe de 0.5 sostenido en 4 años con regímenes distintos ya es una ventaja real. Pedir más dejaría fuera casi todo; pedir menos dejaría pasar ruido.

---

## Criterio 2: drawdown p95 por debajo del límite del perfil

### Drawdown
La **peor caída desde un máximo**. Si tu cuenta llegó a $12,000 y luego bajó a $9,000, el drawdown fue de 25 %, aunque después se recupere.

### El problema de un solo backtest
El backtest muestra **un solo orden** de las operaciones. Si los 5 peores trades hubieran caído seguidos, la caída habría sido mucho peor. Ese orden fue suerte.

### Monte Carlo y el p95
1. Se toman los resultados de la estrategia y se **revuelven 10,000 veces**.
2. Cada orden distinto produce un drawdown distinto: salen 10,000 drawdowns posibles.
3. El **p95** es el valor que el 95 % de esas simulaciones **no rebasa**. Es "un año malo, pero realista". El **p99** es "un año muy malo".

### El criterio
Si el perfil dice "no tolero caer más de 20 %" y el p95 es 28 %, en un año malo la estrategia va a sacar al usuario de su zona. **Se rechaza aunque gane dinero.**

### ¿Depende de cuánto duran los trades? Sí
**Lo que dura cada trade importa, y mucho.** Hay dos efectos:

1. **Caídas dentro del trade.** Si solo se revuelven los resultados finales de cada trade, se pierde lo que pasó *mientras* estaba abierto. Un trade de 4 meses puede bajar 20 % antes de recuperarse y cerrar en +10 %. El usuario sí vivió esa caída de 20 %, aunque el resultado final diga +10 %.
2. **El tiempo.** Una estrategia con 5 trades al año y otra con 500 no acumulan el riesgo igual. El drawdown hay que medirlo sobre el mismo horizonte de tiempo, por ejemplo por año.

**Cómo lo resolvemos:** el Monte Carlo del drawdown se corre sobre los **retornos diarios del sleeve** (el valor de su cuenta cada día, con las posiciones abiertas valuadas a mercado), no sobre el resultado final de cada trade. Se usa *block bootstrap* con bloques de unas 20 velas, para conservar las rachas. Así la medición incluye las caídas dentro de los trades y se compara por horizonte de tiempo. El módulo `tm_agents.montecarlo` ya acepta cualquier serie de retornos y soporta bloques.

### Cuánto suelen durar los trades

| Estrategia | Duración típica de un trade | Trades por año (aprox.) |
|---|---|---|
| **Trend** (Donchian, velas diarias) | **De 2 semanas a 5 meses** | Pocos (ver la tabla siguiente) |
| **Momentum intradía** | **~30 minutos**: entra al inicio de la última media hora del "día" y sale al cierre | Cientos, si opera casi a diario |
| **Kronos** (velas de 4 h) | **De horas a unos 4 días**, porque pronostica 24 velas de 4 h | Decenas a cientos, según sus umbrales |

**Medición real del TrendAgent** (BTC y ETH, 2021–2024, sin optimizar, con comisiones):

| Periodo del canal | BTC: trades / duración mediana | ETH: trades / duración mediana |
|---|---|---|
| 20 días | 31 / 13 días | 24 / 18 días |
| 50 días | 11 / 52 días | 14 / 42 días |
| 100 días | 7 / 63 días | 6 / 77 días |
| 200 días | 5 / 138 días | 5 / 141 días |

Con trades de varios meses, la caída *dentro* del trade es justo la parte importante. Por eso el Monte Carlo del drawdown va sobre los retornos diarios.

---

## Criterio 3: suficientes trades

### Por qué importa
Con pocos trades, la suerte domina. Con una moneda:
- 10 lanzamientos → sacar 7 águilas o más pasa el **17 %** de las veces.
- 100 lanzamientos → sacar 70 águilas o más pasa el **0.004 %** de las veces.

Con 15 trades, una estrategia mediocre puede parecer genial. Con 100 o más, eso es mucho menos probable.

### El problema con el Trend
La tabla de arriba muestra que el TrendAgent hace **muy pocos trades**. Sus periodos largos (100 y 200 días) hacen de 5 a 7 trades en 4 años. Sumando los 4 periodos y los 2 símbolos da unos 103: apenas pasa el mínimo de 100, y solo si se cuentan todos juntos.

Esto no significa que sea una mala estrategia. Significa que **contar trades no es la mejor forma de medir la evidencia de una estrategia lenta**: sus 4 años out-of-sample son unos 1,460 días de exposición al mercado, y cada día aporta información.

### Alternativa para estrategias de baja frecuencia (aprobada el 7 de noviembre de 2026)
El criterio 3 es **"al menos 100 trades, o, para estrategias de baja frecuencia, al menos 30 trades y 3 años out-of-sample con exposición diaria"**. Está implementado en `evaluate(..., oos_years=...)`. El Sharpe y el drawdown se calcularían sobre los retornos diarios en ambos casos.

---

## Resumen

| Criterio | Se calcula sobre | Umbral |
|---|---|---|
| Sharpe | Retornos diarios out-of-sample del sleeve (16 tramos del walk-forward) | > 0.5, y mayor que el de comprar y mantener* |
| Drawdown p95 | Monte Carlo (block bootstrap) de los retornos diarios del sleeve | < `max_drawdown` del perfil |
| Evidencia | Número de trades y años out-of-sample | ≥ 100 trades, o ≥ 30 trades y ≥ 3 años |

\* La comparación contra comprar y mantener también es una propuesta y queda pendiente de aprobación.

Si cumple los tres, pasa a **shadow**: opera en vivo sin capital al menos 7 días. Después una persona decide si la promueve a **activa** (ver la sección 3.3 de la propuesta).

Implementación: `tm_agents.montecarlo` (`ApprovalCriteria` y `evaluate`) y el BacktestService (#13).
