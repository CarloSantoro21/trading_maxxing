# IntradayMomentumAgent: momentum de la primera a la última media hora

Código: `services/agents/src/tm_agents/strategies/intraday_momentum.py` (registrada como `"intraday_momentum"`). Pruebas: `services/agents/tests/test_intraday_momentum.py`.

**Resumen.** El paper encuentra que en BTC el retorno de la noche más la primera media hora predice el de la última media hora del "día". Con los datos de Binance 2020–2025, la regla genera entre 90 y 190 operaciones al año, y el retorno bruto por operación queda entre −2 y +4 pb. La comisión de Binance Spot (0.1 % por lado, 20 pb por ida y vuelta) se come 5 a 10 veces esa ventaja. El propio paper ya lo advierte: su costo de equilibrio sin apalancamiento es de 3 a 10 pb. **La estrategia se queda en `research`/`lab` y no debe recibir capital** salvo que el backtest formal (#13) muestre algo muy distinto. El backtest formal ya se corrió y lo confirma: Sharpe out-of-sample de −2.56 con comisiones y de −0.54 sin ellas (ver [resultados-intraday-momentum.md](resultados-intraday-momentum.md)).

---

## 1. Qué dice el paper

Shen, D., Urquhart, A. y Wang, P. (2022). *Bitcoin intraday time-series momentum*. **Financial Review**, 57(2), 319–344. DOI [10.1111/fire.12290](https://doi.org/10.1111/fire.12290). Leímos el manuscrito aceptado (versión R2 del 21 sep 2021) del repositorio abierto de la Universidad de Reading: [centaur.reading.ac.uk/100181](https://centaur.reading.ac.uk/100181).

Es la versión cripto de Gao, Han, Li y Zhou (*Market intraday momentum*, JFE 2018), que lo documentaron para el ETF del S&P 500.

### 1.1 Datos

Velas de 1 minuto de BTC/USD en Bitfinex, Bitstamp, CEX.IO, Coinbase y Kraken, de 2013 (o el primer año con liquidez) a 2020-12-31. Agregan a medias horas. Cada día calendario es una observación, incluidos los fines de semana (Bitstamp tiene 2,921 días).

### 1.2 Definición de "día" (el reloj de volumen)

BTC opera 24/7, así que el paper fija una sesión artificial:

- **Apertura**: "seleccionamos la hora de apertura de cada exchange *cuando el volumen da un salto* (*volume spikes*)". Siguen a Elaut et al. (2018) y Baltussen et al. (2021). La tabla 1 da 9:00 ET (Bitstamp y CEX.IO), 9:05 (Bitfinex), 9:15 (Kraken) y 9:40 (Coinbase). Es decir, la mañana de EE. UU.
- **Cierre**: **17:00 ET**, cuando empieza la pausa diaria de 60 minutos de los futuros de BTC en CME. Eross et al. (2019) muestran que después de las 16:00 ET todavía hay volumen relevante.

El paper dice "EST". Lo interpretamos como hora del Este con cambio de horario (la pausa de CME sigue el horario local de Chicago).

### 1.3 Qué retorno predice a cuál

Con p(T) el precio en el instante T del día t:

| Retorno | Definición | Ecuación |
|---|---|---|
| r_ONFH | p(apertura + 30 min, t) / p(17:00, t−1) − 1 | (1): la noche más la primera media hora |
| r_SLH | p(16:30, t) / p(16:00, t) − 1 | (2): la penúltima media hora |
| r_LH | p(17:00, t) / p(16:30, t) − 1 | (3): la última media hora |

Regresiones agrupadas (*pooled*) de los cinco exchanges, tabla 3:

- r_LH = α + β·r_ONFH: **β > 0** (t de Newey-West de 4.38), R² = 1.44 %, R² fuera de muestra = 1.09 %.
- r_LH = α + β·r_SLH: **β < 0** (t = −10.2). Es **antipersistencia**: la penúltima media hora **no** predice momentum, predice reversión.
- Con ambos predictores: R² = 2.32 %, R² fuera de muestra = 1.81 %. Se complementan.
- Tabla 4: el efecto es más fuerte cuando la primera media hora tiene más volumen o más volatilidad (β_ONFH = 2.01 en el tercil alto de volumen y 0.43, no significativo, en el bajo).

### 1.4 Regla de trading (sección 3.4, ecuaciones 7 y 8)

Solo se opera la última media hora: se entra a las 16:30 y se cierra a las 17:00.

- **η(r_ONFH)**: largo si r_ONFH > 0, corto si r_ONFH ≤ 0.
- **η(r_SLH)**: largo si r_SLH < 0, corto si r_SLH ≥ 0.
- **η(r_ONFH, r_SLH)**: largo si r_ONFH > 0 **y** r_SLH < 0; corto si r_ONFH ≤ 0 y r_SLH ≥ 0; fuera en otro caso.

Resultados de la tabla 5 (anualizados, sin costos): 7.8 %, 17.3 % y 16.7 % al año, con Sharpe de 0.65, 1.72 y 1.72. Tabla 8: **costo de equilibrio de 3, 7 y 10 pb** por operación sin apalancamiento, contra los 25 pb de comisión de Bitstamp. Los autores concluyen que sin margen **no es rentable**. Solo con apalancamiento de 10:1 el costo de equilibrio sube a 29–96 pb.

---

## 2. Adaptación en trading_maxxing

| Tema | Paper | Nosotros |
|---|---|---|
| Exchange y datos | 5 exchanges BTC/USD, 2013–2020, 1 min agregado | Binance BTC/USDT y ETH/USDT, 2020–2025, velas de 30 min de `tm_marketdata` |
| Apertura | Salto de volumen, elegido a mano por exchange (9:00–9:40 ET) | `open_time="auto"`: el slot ET de 30 min con el mayor cociente volumen(slot) / volumen(slot previo), dentro de `open_window` (por defecto 00:00–15:00 ET), sobre los últimos `volume_lookback_days` (90) días cerrados. Si hay menos de `min_profile_days` (7) días, se usa `default_open` = 9:00 ET. También acepta una hora fija (`open_time="09:30"`). |
| Cierre | 17:00 ET (pausa de CME) | Igual: 17:00 ET, con `America/New_York`, así que en UTC son 21:00 en verano y 22:00 en invierno |
| Granularidad de la apertura | Minutos (9:05, 9:15, 9:40) | Medias horas: con velas de 30 min no se puede abrir a las 9:05 |
| Retornos | Ecuaciones (1)–(3) | Las mismas, con p(T) = cierre de la vela que termina en T (`session_table`) |
| Regla | Largo/corto (ec. 7 y 8) | `mode="onfh"` (por defecto), `"slh"` o `"both"`. **Spot: solo la pata larga.** Cuando el paper iría en corto, la señal es `flat` (USDT) y `metadata.paper_position = -1` lo registra. |
| Umbral | No hay | `threshold` opcional: exige r_ONFH > umbral (útil para filtrar ruido y bajar comisiones) |
| Confianza | No aplica | min(1, \|r_ONFH\| / σ), con σ la desviación de r_ONFH en los últimos 90 días |

**Por qué `open_window` termina a las 15:00 ET.** La apertura tiene que quedar dentro de la sesión que cierra a las 17:00 y antes de la penúltima media hora. Sin esa ventana, el segundo salto de volumen más grande de Binance (18:00 ET, cuando abre Asia: de 1.41 % a 1.87 % de participación, 1.32× en BTC y 1.36× en ETH) caería después del cierre. Eso rompería la definición de "día" del paper.

**Qué emite el agente.** Cada 30 minutos devuelve un `Signal`:

- `long` solo si `ctx.now` cae en [16:30, 17:00) ET y la regla se cumple. `metadata.exit_at` trae la hora de salida (17:00 ET en UTC). La decisión de compra usa velas ya cerradas: la última es la de las 16:00, que cierra a las 16:30.
- `flat` en cualquier otro momento. El ejecutor vende a USDT al cierre de las 17:00 ET.
- `metadata` incluye `open_time_et`, `open_source` (`fixed`, `volume` o `default`), `r_onfh`, `r_slh`, `paper_position`, `reason` y `bars_used`.

**Sin look-ahead.** `closed_bars` descarta las velas que no han cerrado en `ctx.now`, y el perfil de volumen también usa solo velas cerradas. La prueba `test_no_look_ahead` comprueba que cambiar la vela de las 16:30 o el día siguiente no altera la señal.

### 2.1 El reloj de volumen en Binance

Participación promedio de cada media hora en el volumen de su día ET, 2020–2025:

| Slot ET | BTC | ETH | Salto contra el slot previo |
|---|---|---|---|
| 09:00 | 2.51 % | 2.47 % | 0.94× / 0.92× |
| **09:30** | **3.52 %** | **3.42 %** | **1.40× / 1.39×** (el máximo dentro de la ventana) |
| 10:00 | 3.41 % | 3.31 % | 0.97× |
| 16:30 | 1.76 % | 1.72 % | — |

El salto está en la apertura de la bolsa de Nueva York (9:30 ET). Sale igual en 2020–2022, en 2023–2025 y en toda la muestra, así que elegirlo con todos los datos no introduce un sesgo práctico. Cae dentro del rango del paper (9:00–9:40 ET).

---

## 3. Chequeo rápido con datos reales (no es el backtest formal del #13)

Retornos simples sumados, sin capitalizar y sin deslizamiento (*slippage*), con la comisión de **0.1 % por lado** (20 pb por operación de ida y vuelta) y apertura a las 9:30 ET. Solo es un orden de magnitud para saber si las comisiones se comen la ventaja. In-sample: 2020–2022; out-of-sample: 2023–2025 (la apertura se eligió con el in-sample).

### BTC/USDT

| Periodo | Regla | Operaciones al año | Bruto por op. | Neto por op. | Bruto anual | Comisiones anuales | Neto anual |
|---|---|---|---|---|---|---|---|
| 2020–2025 | onfh | 190 | −0.5 pb | −20.5 pb | −1.0 % | 38.0 % | **−39.0 %** |
| 2020–2025 | slh | 173 | +0.1 pb | −19.9 pb | +0.2 % | 34.5 % | **−34.3 %** |
| 2020–2025 | both | 95 | +1.3 pb | −18.7 pb | +1.2 % | 18.9 % | **−17.7 %** |
| IS 2020–2022 | onfh | 187 | −1.8 pb | −21.8 pb | −3.4 % | 37.3 % | −40.7 % |
| OOS 2023–2025 | onfh | 194 | +0.7 pb | −19.3 pb | +1.4 % | 38.8 % | −37.4 % |
| OOS 2023–2025 | both | 98 | +2.1 pb | −18.0 pb | +2.0 % | 19.6 % | −17.5 % |

### ETH/USDT

| Periodo | Regla | Operaciones al año | Bruto por op. | Neto por op. | Bruto anual | Comisiones anuales | Neto anual |
|---|---|---|---|---|---|---|---|
| 2020–2025 | onfh | 189 | −0.4 pb | −20.4 pb | −0.7 % | 37.8 % | **−38.5 %** |
| 2020–2025 | slh | 168 | −0.3 pb | −20.3 pb | −0.4 % | 33.6 % | **−34.0 %** |
| 2020–2025 | both | 94 | +2.5 pb | −17.5 pb | +2.4 % | 18.9 % | **−16.5 %** |
| IS 2020–2022 | both | 91 | +4.0 pb | −16.0 pb | +3.7 % | 18.3 % | −14.6 % |
| OOS 2023–2025 | onfh | 193 | −1.2 pb | −21.2 pb | −2.3 % | 38.5 % | −40.7 % |
| OOS 2023–2025 | both | 97 | +1.1 pb | −18.9 pb | +1.1 % | 19.5 % | −18.4 % |

### Regresión de la ecuación 5 (r_LH = α + β₁·r_ONFH + β₂·r_SLH)

| | β₁ (ONFH) | β₂ (SLH) | R² |
|---|---|---|---|
| BTC IS 2020–2022 | +0.017 | −0.032 | 1.01 % |
| BTC OOS 2023–2025 | −0.002 | +0.003 | 0.01 % |
| ETH IS 2020–2022 | +0.018 | −0.047 | 1.32 % |
| ETH OOS 2023–2025 | −0.008 | +0.028 | 0.24 % |

Los β del paper (0.968 y −9.78) están en unidades anualizadas, así que no se comparan directamente con estos. Lo comparable son los signos y el R². No calculamos errores de Newey-West.

### Lectura

1. **Frecuencia.** La regla `onfh` en spot entra casi uno de cada dos días: unas 190 operaciones al año, 380 órdenes. `both` lo baja a unas 95.
2. **Las comisiones se comen todo.** El bruto por operación nunca pasa de 4 pb, y el costo es de 20 pb. El costo de equilibrio por lado queda entre −0.9 y +2 pb, contra 10 pb de comisión. Las comisiones restan entre 19 % y 39 % al año. Coincide con el paper: 3–10 pb de equilibrio sin margen.
3. **El efecto se debilitó.** In-sample (2020–2022) aparecen los signos del paper (β₁ > 0, β₂ < 0, R² ≈ 1 %). Out-of-sample (2023–2025) los coeficientes cambian de signo y el R² se va a cero. Es lo esperable para una anomalía publicada en 2021–2022.
4. **El paper usa cortos.** Buena parte de su rendimiento viene de la pata corta (la tabla 5 compara contra "Always long", que gana 6.5 % en la última media hora). En spot esa pata se pierde. En Binance, la última media hora tiene media negativa (−1.1 pb en BTC y −2.1 pb en ETH), así que la pata larga sola tiene todavía menos ventaja.
5. **Conclusión.** Con 0.1 % por lado no hay ventaja que explotar. Para revivirla haría falta una comisión mucho menor (cuenta *maker* con BNB o VIP), un umbral que deje solo los días de r_ONFH grande (el efecto es más fuerte con volumen o volatilidad alta, tabla 4) o apalancamiento. Lo último queda fuera del alcance de Binance Spot. El backtest formal del #13 (walk-forward, Monte Carlo y criterios de aprobación) debe confirmarlo antes de descartarla.

### Cómo reproducirlo

```python
import glob
import pandas as pd
from tm_agents.strategies.intraday_momentum import fee_check, session_table, volume_clock_open

files = sorted(glob.glob("data/bars/BTCUSDT/30m/*.parquet"))
bars = pd.concat(pd.read_parquet(f) for f in files)
open_slot = volume_clock_open(bars[bars.index < "2023-01-01"])  # 19 = 09:30 ET
sessions = session_table(bars, open_slot)
fee_check(sessions[sessions.index >= "2023-01-01"], fee_per_side=0.001, mode="onfh")
```

---

## 4. Diferencias y limitaciones

- **Sin cortos.** Solo se replica la pata larga de las ecuaciones 7 y 8.
- **Apertura en medias horas.** El paper usa aperturas al minuto. Nosotros redondeamos al slot de 30 minutos con mayor salto de volumen, que en Binance es 9:30 ET.
- **Otro exchange y otro periodo.** Binance USDT (2020–2025) en lugar de exchanges USD (2013–2020). Binance tiene más volumen asiático, y el salto de las 18:00 ET (00:00 UTC en invierno) es casi tan grande como el de las 9:30.
- **Cambios de horario.** Las horas que faltan o se repiten caen a las 2:00 ET, así que no tocan la apertura, el cierre ni las velas de la regla.
- **Chequeo rápido, no backtest.** No hay deslizamiento, capitalización, Monte Carlo ni walk-forward. Eso le toca al laboratorio del #13.
