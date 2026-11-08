# Investigación de estrategias nuevas

**Resumen.** Revisamos 11 familias de estrategias contra el filtro del proyecto: Binance Spot (solo comprado o en USDT), BTC/USDT y ETH/USDT, 0.1 % de comisión por lado y evidencia publicada específica de cripto. **Ninguna de las estrategias con evidencia sólida está de verdad poco correlacionada con el TrendAgent.** Las que sí tienen baja correlación (reversión tras caídas, calendario, filtro de funding) no tienen evidencia firme o no funcionan con nuestros datos. Recomendamos tres, en este orden:

1. **Momentum de serie de tiempo con rebalanceo semanal** (Liu y Tsyvinski, RFS 2021): un sleeve nuevo. Tiene la correlación más baja con el trend (0.62 a 0.84, según la ventana) entre las que tienen ventaja.
2. **Regla de media móvil** (Detzel et al., Financial Management 2021): más votos para el conjunto del trend. Correlación de 0.87 a 0.91.
3. **Volatility targeting del trend** (Grobys et al., FMPM 2025; Zarattini et al., SFI 2025): baja prioridad. Baja el drawdown, pero en nuestros datos no mejora el Sharpe.

Todos los números del cálculo rápido (sección 5) son un **orden de magnitud**, no el backtest formal del laboratorio (#13).

---

## 1. Filtro

| Requisito | Por qué |
|---|---|
| Solo largo o USDT en Binance Spot | Sin cortos, apalancamiento, futuros ni opciones. Los datos de futuros (funding, basis) sirven como **señal** si son públicos, gratuitos y tienen histórico. |
| Evidencia **de cripto**, de preferencia revisada por pares o con prueba out-of-sample | La propuesta (sección 2.1) solo admite estrategias con evidencia en cripto. |
| Pocas operaciones | Con 20 pb por ida y vuelta, 100 operaciones al año cuestan 20 % anual. El momentum intradía murió así (ver `intraday-momentum.md`). |
| Complementaria al TrendAgent | Poco correlacionada con el trend, o que lo mejore. |
| Datos | Tenemos velas OHLCV de Binance 2020–2025 (1m, 15m, 30m, 1d). Cualquier otro dato necesita fuente, costo e histórico. |

Todas las citas se verificaron en Crossref (título, autores, revista, volumen y páginas) y en la página del editor o en un repositorio abierto. Cuando no pudimos leer la fuente primaria, lo decimos.

---

## 2. Tabla comparativa

"Correlación con el trend" es la correlación de los retornos diarios netos contra el TrendAgent en 2021–2024 (sección 5), salvo donde dice "esperada". "Operaciones" son idas y vueltas completas de exposición al año, por símbolo.

| Estrategia | Evidencia | Tipo | ¿Cripto? | ¿Spot? | Operaciones al año / peso de comisiones | Datos (¿gratis?) | Correlación con el trend | Complejidad | Recomendación |
|---|---|---|---|---|---|---|---|---|---|
| **Momentum de serie de tiempo semanal** (comprado si el retorno de la última semana o del último mes es > 0; rebalanceo semanal) | Liu, Y. y Tsyvinski, A. (2021). *Risks and Returns of Cryptocurrency*. Review of Financial Studies, 34(6), 2689–2727. [doi.org/10.1093/rfs/hhaa113](https://doi.org/10.1093/rfs/hhaa113). Leímos el NBER WP 24877, tablas 14 a 17 | Revisión por pares; incluye una prueba out-of-sample (tabla 17) | Sí (BTC; ETH más débil) | Sí: solo la pata larga | 5 a 14; de 1.0 a 2.8 % al año | OHLCV 1d (sí) | 0.62–0.66 (7 días, domingo); 0.79–0.84 (7 tramos o 28 días) | Baja | **Sí, #1** |
| **Regla de media móvil** (comprado si el cierre está sobre su SMA de N días) | Detzel, A., Liu, H., Strauss, J., Zhou, G. y Zhu, Y. (2021). *Learning and predictability via technical analysis: Evidence from bitcoin and stocks with hard-to-value fundamentals*. Financial Management, 50(1), 107–137. [doi.org/10.1111/fima.12310](https://doi.org/10.1111/fima.12310) | Revisión por pares; predicción in-sample y out-of-sample de BTC | Sí (BTC) | Sí: largo o efectivo, igual que en el paper | 6 a 13 con SMA 50–100 (1.2 a 2.6 %); 20 con SMA 20 (4 %) | OHLCV 1d (sí) | 0.87–0.91 (SMA 50 y 100) | Baja | **Sí, #2**, como votos del trend |
| **Volatility targeting del trend** (exposición × min(1, objetivo / vol. realizada)) | Grobys, K., Kolari, J. W., Sandretto, D., Shahzad, S. J. H. y Äijö, J. (2025). *Cryptocurrency momentum has (not) its moments*. Financial Markets and Portfolio Management, 39(4), 443–476. [doi.org/10.1007/s11408-025-00474-9](https://doi.org/10.1007/s11408-025-00474-9). Zarattini, C., Pagani, A. y Barbon, A. (2025). *Catching Crypto Trends*. SFI Research Paper 25-80. [ideas.repec.org/p/chf/rpseri/rp2580.html](https://ideas.repec.org/p/chf/rpseri/rp2580.html). Base en acciones: Moreira y Muir (2017), JF 72(4), [10.1111/jofi.12513](https://doi.org/10.1111/jofi.12513) | Revisión por pares (Grobys, momentum *cross-sectional*) y working paper (Zarattini) | Sí | Sí, si el escalado es ≤ 1 | Casi las mismas que el trend (2 a 3) con una banda de rebalanceo del 10 %; 0.4 a 0.6 % al año | OHLCV 1d (sí) | 0.94–0.98 | Baja | **Sí, #3, prioridad baja** |
| Rotación BTC/ETH/USDT (*dual momentum*) | Sin artículo de revista en cripto. Solo encontramos un paper de congreso: Singh, Thulasiram, Thavaneswaran y Paseka, *Comparison of Trading Strategies: Dual Momentum vs Pairs Trading*, COMPSAC 2023, [10.1109/COMPSAC57700.2023.00207](https://doi.org/10.1109/COMPSAC57700.2023.00207). Solo leímos el resumen. Liu, Tsyvinski y Wu (2022), *Common Risk Factors in Cryptocurrency*, JF 77(2), 1133–1177, [10.1111/jofi.13119](https://doi.org/10.1111/jofi.13119), es momentum *cross-sectional* largo-corto sobre cientos de monedas, no una rotación de dos activos | Congreso / no aplica | Parcial | Sí | 6 a 12 (1 a 2.4 %) | OHLCV 1d (sí) | 0.74–0.76 | Media | **No por ahora**: falta evidencia. Los números son buenos (sección 5.4) |
| Factores de cripto *cross-sectional* (tamaño, momentum, etc.) | Liu, Tsyvinski y Wu (2022), ver arriba | Revisión por pares | Sí | **No**: son largo-corto y necesitan muchas monedas | — | Cientos de monedas | — | Alta | **No**: no cabe con dos activos y sin cortos |
| Reversión tras caídas extremas (comprar si el día cae más de k·σ y sostener H días) | Corbet, S. y Katsiampa, P. (2020). *Asymmetric mean reversion of Bitcoin price returns*. IRFA, 71, 101267, [10.1016/j.irfa.2018.10.004](https://doi.org/10.1016/j.irfa.2018.10.004). Kellner, T. y Maltritz, D. (2022). *A broad analysis of short-term overreactions in the market for cryptocurrencies*. J. of Economic Studies, 49(8), 1585–1608, [10.1108/JES-09-2021-0488](https://doi.org/10.1108/JES-09-2021-0488). En contra: Caporale, G. M. y Plastun, A. (2020). *Momentum effects in the cryptocurrency market after one-day abnormal returns*. FMPM, 34(3), 251–266, [10.1007/s11408-020-00357-1](https://doi.org/10.1007/s11408-020-00357-1): después de días anormales encuentran **momentum**, salvo en BTC al alza y ETH a la baja | Revisión por pares, con resultados mixtos y sin regla out-of-sample | Sí | Sí | 4 a 14 (0.9 a 2.8 %) | OHLCV 1d (sí) | 0.05–0.28 | Baja | **No**: en 2021–2024 el neto va de −16 a +13 % al año, según los parámetros, y el signo no es estable |
| Reversión de corto plazo (diaria) | Zaremba, A., Bilgin, M. H., Long, H., Mercik, A. y Szczygielski, J. J. (2021). *Up or down? Short-term reversal, momentum, and liquidity effects in cryptocurrency markets*. IRFA, 78, 101908, [10.1016/j.irfa.2021.101908](https://doi.org/10.1016/j.irfa.2021.101908). Datos diarios de más de 3,600 monedas. Solo leímos el resumen: la reversión viene de las monedas ilíquidas, y en las grandes hay momentum diario | Revisión por pares (no leímos el texto completo) | Sí | Sí | Cientos: la comisión se come todo | OHLCV (sí) | Esperada baja | Baja | **No**: en BTC y ETH el efecto no aplica y la frecuencia es incompatible con las comisiones |
| Señales on-chain (MVRV, flujos a exchanges) | No encontramos un estudio revisado por pares sobre el MVRV. Flujos: Hoang, L. T. y Baur, D. G., *Effects of Bitcoin Exchange Reserves on Bitcoin Returns and Volatility*, SSRN 3902504 (working paper; no confirmamos que esté publicado). arXiv 2411.06327 (2024): el flujo neto a exchanges **no** predice el retorno de BTC (sí el de ETH, con signo negativo) | Working paper o blog | Sí | Sí | El MVRV es de ciclo: unas 1 a 3 señales por ciclo de 4 años | MVRV: [Coin Metrics Community](https://docs.coinmetrics.io/packages/coin-metrics-community-data) (gratis, con límites). Flujos y reservas: Glassnode o CryptoQuant (de pago para el histórico completo) | Esperada media | Media | **No**: evidencia débil, muy pocas señales para el criterio 3 y datos de pago |
| Funding rate o basis como filtro de régimen (salir del trend cuando el funding está en su percentil 90) | Schmeling, M., Schrimpf, A. y Todorov, K. (2026). *Crypto Carry*. Management Science (online, mayo de 2026), [10.1287/mnsc.2024.05069](https://doi.org/10.1287/mnsc.2024.05069). La versión BIS WP 1087 (2023) dice que "un carry alto predice caídas futuras"; solo leímos el resumen | Revisión por pares | Sí | Sí, como señal | 4 a 6 (0.9 a 1.2 %) | Funding de Binance USDⓈ-M: gratis desde enero de 2020 en [data.binance.vision](https://data.binance.vision/?prefix=data/futures/um/monthly/fundingRate/) (zips mensuales; lo descargamos) | 0.71–0.77 como filtro | Media | **No por ahora**: en nuestros datos baja el Sharpe del trend en BTC (0.82 → 0.62) y en ETH la relación va al revés (sección 5.5) |
| Efectos de calendario (fin de semana, lunes, cambio de mes) | Baur, Cahill, Godfrey y Liu (2019). *Bitcoin time-of-day, day-of-week and month-of-year effects in returns and trading volume*. FRL, 31, 78–92, [10.1016/j.frl.2019.04.023](https://doi.org/10.1016/j.frl.2019.04.023): no hay patrones persistentes. Caporale, G. M. y Plastun, A. (2019). *The day of the week effect in the cryptocurrency market*. FRL, 31, [10.1016/j.frl.2018.11.012](https://doi.org/10.1016/j.frl.2018.11.012): el lunes de BTC no se distingue de operar al azar. No encontramos estudios del cambio de mes en cripto | Revisión por pares, en contra | Sí | Sí | ~52 (10 % al año) | OHLCV (sí) | Esperada baja | Baja | **No**: la evidencia publicada lo descarta |
| Indicadores de volumen | Balcilar, M., Bouri, E., Gupta, R. y Roubaud, D. (2017). *Can volume predict Bitcoin returns and volatility? A quantiles-based approach*. Economic Modelling, 64, 74–81, [10.1016/j.econmod.2017.03.019](https://doi.org/10.1016/j.econmod.2017.03.019): el volumen predice el retorno solo en el centro de la distribución, no en mercados alcistas ni bajistas, y no predice la volatilidad. El TSMOM ponderado por volumen (Huang, Sangiorgi y Urquhart) solo aparece en una tesis embargada; no encontramos el artículo | Revisión por pares, sin regla de trading | Sí | Sí | — | OHLCV (sí) | — | Media | **No**: no hay una regla probada |
| Filtro de régimen de volatilidad (operar solo con vol. baja o alta) | No encontramos evidencia de cripto de un filtro encendido/apagado. La versión continua es el volatility targeting (#3) | — | — | Sí | — | OHLCV (sí) | — | Baja | **No como estrategia aparte**: ya lo cubre el #3 |

Otras dos referencias que pesan en contra de las reglas técnicas en BTC:

- Hudson, R. y Urquhart, A. (2021). *Technical trading and cryptocurrencies*. Annals of Operations Research, 297(1–2), 191–220, [10.1007/s10479-019-03357-1](https://doi.org/10.1007/s10479-019-03357-1). Prueban unas 15,000 reglas: hay rentabilidad in-sample, pero **BTC no muestra predictibilidad out-of-sample** (otras monedas sí).
- Grobys, K., Ahmed, S. y Sapkota, N. (2020). *Technical trading rules in the cryptocurrency market*. FRL, 32, 101396, [10.1016/j.frl.2019.101396](https://doi.org/10.1016/j.frl.2019.101396). La SMA de 20 días funciona en 2016–2018, pero **solo excluyendo a BTC**.

---

## 3. Candidatas recomendadas

### 3.1 #1: Momentum de serie de tiempo con rebalanceo semanal (TsmomAgent)

**Evidencia.** Liu y Tsyvinski (RFS 2021), sección 4.1, versión NBER WP 24877:

- Tabla 14: en BTC, el retorno de esta semana predice el de las semanas 1, 2, 3 y 4 siguientes, con coeficientes de 0.19, 0.22, 0.21 y 0.09, y t de 3.7, 4.5, 4.3 y 1.7.
- Tabla 15: ordenando las semanas por quintil de retorno, el quintil alto gana 11.2 % la semana siguiente (Sharpe semanal de 0.45) y el bajo, 2.6 % (0.19).
- Tabla 17: con los cortes de quintil fijados con los primeros dos años (sin look-ahead), el efecto se mantiene: 7.9 % contra 3.4 %.
- Su muestra termina en 2018, así que **2020–2025 es out-of-sample para el paper**.
- En ETH solo reportan momentum diario (tabla 18), y es más débil.

**Regla.**

- Cada domingo a las 00:00 UTC (cierre de la vela diaria del domingo), r = cierre / cierre de hace L días − 1.
- Si r > 0: `long` la semana siguiente. Si no: `flat` (USDT).
- **Variante robusta (7 tramos):** el sleeve se divide en 7 tramos iguales, uno por día de la semana. Cada tramo aplica la regla en su día y mantiene la decisión 7 días. La confianza es la fracción de tramos en largo.
- Parámetros: L ∈ {7, 14, 28} días (el paper usa 7) y anclaje ∈ {domingo, 7 tramos}. Se eligen dentro del walk-forward, nunca con todo el histórico.

**Por qué debería funcionar.** Es la misma subreacción o atención de inversores que explica el trend (Liu y Tsyvinski también documentan que la atención en Google predice retornos), pero a un horizonte de semanas y no de meses. Reacciona antes que los canales de 100 y 200 días, sin llegar a la frecuencia intradía que mató al momentum de 30 minutos.

**Riesgos.**

- **Sensibilidad al día de anclaje.** Con L = 7 en BTC 2021–2024, el Sharpe va de 0.18 (miércoles) a 1.03 (jueves) según el día. El domingo del paper da 0.72. Por eso la variante de 7 tramos es la candidata principal, aunque su Sharpe es más bajo (0.57 en BTC y 0.86 en ETH).
- **Correlación.** Con L = 28 el Sharpe es mejor (1.04 en BTC y 1.08 en ETH en 2021–2024), pero la correlación con el trend sube a 0.84. Es "un trend más rápido": complementa poco.
- La evidencia de ETH es más débil en el paper.
- 13 operaciones al año con L = 7 cuestan ~2.7 % anual.

### 3.2 #2: Regla de media móvil como votos del trend (MovingAverageAgent)

**Evidencia.** Detzel et al. (Financial Management 2021): el cociente precio / media móvil predice el retorno diario de BTC in-sample y out-of-sample, y las estrategias basadas en él dan alfa y más Sharpe que comprar y mantener. La versión de trabajo de 2018 (resumen de [CXO Advisory](https://www.cxoadvisory.com/currency-trading/timing-bitcoin-with-smas/)) prueba SMA de 5, 10, 20, 50 y 100 días en 2010–2017, en bruto:

- Sharpe de 2.6 (SMA 5) a 2.2 (SMA 100), contra 2.0 de comprar y mantener.
- Fricción de equilibrio de 1.1 % (SMA 5) a 4.5 % (SMA 100) por cambio de posición.

No leímos el PDF publicado, así que los números por ventana de la versión de revista no están verificados. **En contra:** Hudson y Urquhart (2021) no encuentran predictibilidad out-of-sample en BTC, y Grobys et al. (2020) solo la encuentran excluyendo a BTC.

**Regla.**

- Al cierre diario: `long` si cierre > SMA(N); `flat` en otro caso.
- N ∈ {50, 100}. Descartamos N = 20: hace 20 operaciones al año y pierde el 4 % anual en comisiones.
- **Integración recomendada:** agregar los votos SMA 50 y SMA 100 al conjunto del TrendAgent (6 votos en lugar de 4) o correr un sleeve aparte con la mezcla. La correlación de 0.87 a 0.91 hace que un sleeve independiente aporte poca diversificación.

**Por qué debería funcionar.** El modelo de Detzel et al. explica el análisis técnico por aprendizaje racional cuando los fundamentales son difíciles de valuar, como en BTC. Además, la SMA sale por un criterio distinto al punto medio del canal Donchian (promedio contra extremos), así que suaviza los cambios de voto.

**Riesgos.**

- Los resultados del paper son anteriores a 2018 y hay evidencia publicada en contra para BTC.
- Elegir N mirando 2021–2024 es *data snooping*: N debe elegirse en el walk-forward.
- La mejora es pequeña y depende del periodo.

### 3.3 #3: Volatility targeting del trend (prioridad baja)

**Evidencia.**

- Grobys et al. (FMPM 2025): en momentum de cripto de alta capitalización, que sufre caídas fuertes, la gestión por volatilidad "ayuda a mitigar las caídas" (resumen del repositorio de la Universidad de Vaasa; no pudimos leer el texto completo por un bloqueo anti-bots). Ojo: es momentum **cross-sectional** largo-corto, no un trend largo/flat.
- Zarattini, Pagani y Barbon (SFI 25-80) combinan justo nuestro conjunto Donchian con un dimensionamiento por volatilidad. Las fuentes secundarias no coinciden en el objetivo (20 % o 25 % anual, con una ventana de 90 días), y no pudimos verificarlo en el PDF.
- La base teórica (Moreira y Muir, 2017) es de acciones, y Cederburg et al. (2020, citado por Grobys) encuentran que en tiempo real a menudo no mejora.

**Regla.**

- exposición = confianza del trend × min(1, σ\* / σ̂₃₀). Aquí σ̂₃₀ es la volatilidad anualizada de los últimos 30 días, que el TrendAgent ya publica en `metadata.annualized_vol`, y σ\* es el objetivo, entre 40 y 60 %.
- Solo se rebalancea si el cambio de exposición pasa del 10 % o si cambia el voto del trend.
- Sin apalancamiento: el escalado nunca pasa de 1.

**Por qué podría ayudar.** En cripto, la volatilidad es persistente y las caídas fuertes llegan con volatilidad alta. Bajar la exposición en esos tramos recorta el drawdown.

**Riesgos y por qué tiene prioridad baja.**

- En nuestros datos el **Sharpe casi no cambia**: BTC pasa de 0.82 a 0.80 y ETH de 0.94 a 1.01 en 2021–2024. En 2025 empeora en ambos.
- Lo que sí cambia es el drawdown: BTC pasa de −42 a −32 % y ETH de −44 a −24 %.
- La regla de exposición por perfil (sección 3.4 de la propuesta, `min(1, drawdown del nivel / drawdown p95)`) ya escala el sleeve de forma estática. Con un Sharpe igual, el volatility targeting termina dando casi el mismo retorno por unidad de drawdown. Solo vale la pena si el walk-forward muestra una mejora de Sharpe o de drawdown p95 que el escalado estático no logra.
- Tiene sentido implementarlo como parámetro del TrendAgent o del RiskAgent, y no como estrategia aparte.

---

## 4. Ranking final

| # | Estrategia | Tipo | Por qué en este lugar |
|---|---|---|---|
| 1 | **TsmomAgent**: momentum de serie de tiempo semanal (Liu y Tsyvinski, RFS 2021) | Sleeve nuevo, 1d, rebalanceo semanal | Evidencia revisada por pares con prueba out-of-sample, y nuestros datos de 2020–2025 son out-of-sample para el paper. Es la candidata con ventaja que tiene la correlación más baja con el trend. Hace pocas operaciones. |
| 2 | **MovingAverageAgent**: regla SMA 50/100 (Detzel et al., FM 2021) | Votos extra del trend o sleeve propio | Revisión por pares y predicción out-of-sample, pero hay evidencia en contra para BTC y la correlación con el trend es alta. Mezclada con el trend, mejoró el Sharpe en los 4 cortes (BTC y ETH, 2021–2024 y 2025). |
| 3 | **Volatility targeting del trend** (Grobys et al., FMPM 2025; Zarattini et al., SFI 2025) | Parámetro del TrendAgent o del RiskAgent | Barato y con evidencia en cripto para recortar caídas. En nuestros datos no mejora el Sharpe, así que solo entra si el laboratorio lo justifica. |

**No entran:** la rotación BTC/ETH (le falta evidencia publicada, aunque sus números son buenos; se puede revisar si aparece), el filtro de funding (la evidencia existe, pero nuestros datos la contradicen), la reversión tras caídas, la reversión de corto plazo, el on-chain, el calendario, el volumen, el filtro de régimen de volatilidad y los factores cross-sectional.

---

## 5. Cálculo rápido con datos reales (no es el backtest formal)

**Método.**

- Datos: velas diarias de Binance (`data/bars/<SYMBOL>/1d/*.parquet`).
- Trend: `donchian_states` del `TrendAgent` (20, 50, 100 y 200 días, sin cortos). Su exposición es la fracción de canales en largo (0, 0.25, …, 1), igual que la confianza de la señal.
- La posición se decide al cierre del día t y se aplica al retorno del día t+1.
- Comisión: 0.1 % por lado sobre el cambio de exposición. Sin deslizamiento, sin capitalización en el Sharpe (media / desviación diaria × √365) y sin walk-forward.
- "Operaciones al año" son idas y vueltas equivalentes: rotación ÷ 2.
- "Corr. trend" es la correlación de retornos diarios netos contra el trend del mismo símbolo.
- Periodos: 2021–2024 (los tramos de prueba del walk-forward) y 2025 (el holdout). **Los parámetros alternativos se probaron con estos mismos datos, así que hay data snooping: los números son un orden de magnitud.**

Para reproducirlo: `uv run python docs/estrategias/chequeo_nuevas_estrategias.py data/bars [funding.csv]`. El docstring del script explica cómo armar `funding.csv`.

### 5.1 Resumen de las candidatas del ranking

**BTC/USDT, 2021–2024**

| Estrategia | Operaciones al año | Bruto % al año | Neto % al año | Comisiones % al año | Sharpe neto | Drawdown máx. | Corr. trend |
|---|---|---|---|---|---|---|---|
| Comprar y mantener | 0 | 48.6 | 48.6 | 0 | 0.78 | −76.6 % | 0.71 |
| **Trend (base)** | 3.2 | 29.3 | 28.7 | 0.6 | **0.82** | −42.1 % | 1.00 |
| #1 TSMOM 7 d, domingo (paper) | 13.6 | 34.8 | 32.1 | 2.7 | 0.72 | −55.3 % | **0.66** |
| #1 TSMOM 7 d, 7 tramos | 13.7 | 24.8 | 22.1 | 2.7 | 0.57 | −61.5 % | 0.79 |
| #1 TSMOM 28 d, 7 tramos | 5.3 | 45.0 | 44.0 | 1.1 | 1.04 | −47.4 % | 0.84 |
| #2 SMA 50 d | 9.1 | 50.7 | 48.9 | 1.8 | 1.11 | −58.0 % | 0.88 |
| #2 SMA 100 d | 6.5 | 47.6 | 46.3 | 1.3 | 1.02 | −38.5 % | 0.91 |
| #2 Mezcla trend + SMA 50 | 6.1 | 40.0 | 38.8 | 1.2 | 1.01 | −46.7 % | 0.96 |
| #3 Trend + vol target 40 % | 2.8 | 19.2 | 18.7 | 0.6 | 0.80 | −32.0 % | 0.96 |

**ETH/USDT, 2021–2024**

| Estrategia | Operaciones al año | Bruto % al año | Neto % al año | Comisiones % al año | Sharpe neto | Drawdown máx. | Corr. trend |
|---|---|---|---|---|---|---|---|
| Comprar y mantener | 0 | 69.9 | 69.9 | 0 | 0.87 | −79.3 % | 0.70 |
| **Trend (base)** | 2.9 | 43.3 | 42.7 | 0.6 | **0.94** | −44.1 % | 1.00 |
| #1 TSMOM 7 d, domingo (paper) | 12.2 | 69.7 | 67.3 | 2.5 | 1.25 | −41.6 % | **0.64** |
| #1 TSMOM 7 d, 7 tramos | 13.1 | 45.1 | 42.4 | 2.6 | 0.86 | −43.7 % | 0.81 |
| #1 TSMOM 28 d, 7 tramos | 5.9 | 66.2 | 65.0 | 1.2 | 1.08 | −49.8 % | 0.85 |
| #2 SMA 50 d | 10.9 | 54.4 | 52.2 | 2.2 | 0.91 | −54.6 % | 0.89 |
| #2 SMA 100 d | 9.0 | 44.4 | 42.7 | 1.8 | 0.69 | −74.5 % | 0.87 |
| #2 Mezcla trend + SMA 50 | 6.9 | 48.9 | 47.5 | 1.4 | 0.95 | −46.1 % | 0.97 |
| #3 Trend + vol target 40 % | 2.0 | 24.3 | 23.9 | 0.4 | 1.01 | −24.3 % | 0.94 |

**Holdout 2025 (Sharpe neto; entre paréntesis, el neto % al año)**

| Estrategia | BTC | ETH |
|---|---|---|
| Comprar y mantener | 0.05 (2.1) | 0.21 (16.1) |
| Trend (base) | −0.13 (−2.3) | 0.56 (19.4) |
| #1 TSMOM 7 d, domingo | 0.21 (5.9) | 0.36 (19.9) |
| #1 TSMOM 7 d, 7 tramos | −0.26 (−6.0) | 0.44 (19.8) |
| #1 TSMOM 28 d, 7 tramos | 0.56 (12.2) | 1.32 (59.7) |
| #2 SMA 50 d | 0.30 (7.2) | 1.59 (74.2) |
| #2 Mezcla trend + SMA 50 | 0.13 (2.5) | 1.22 (46.8) |
| #3 Trend + vol target 40 % | −0.21 (−3.6) | 0.33 (7.3) |

### 5.2 Robustez del TSMOM al día de anclaje (Sharpe neto, 2021–2024)

| L | Lun | Mar | Mié | Jue | Vie | Sáb | **Dom** |
|---|---|---|---|---|---|---|---|
| BTC, 7 d | 0.39 | 0.24 | 0.18 | 1.03 | 0.43 | 0.52 | **0.72** |
| BTC, 28 d | 0.72 | 0.79 | 0.85 | 0.89 | 1.12 | 1.09 | **1.43** |
| ETH, 7 d | 0.77 | 0.54 | 0.41 | 0.48 | 0.81 | 0.98 | **1.25** |
| ETH, 28 d | 1.02 | 0.80 | 1.15 | 1.07 | 1.10 | 0.94 | **1.14** |

Con L = 7, el resultado depende mucho del día. Con L = 28 es positivo en todos los anclajes. Por eso el agente debe usar los 7 tramos y elegir L en el walk-forward.

### 5.3 Lectura

1. **Comisiones.** Ninguna de las candidatas pasa de 14 operaciones al año. El costo va de 0.4 a 2.8 % anual, contra 17 a 39 % del momentum intradía. El que más paga es el TSMOM de 7 días (~2.7 %).
2. **Correlación.** Todo lo que tiene ventaja es de la familia del momentum: de 0.62 a 0.97 con el trend. Lo poco correlacionado (reversión tras caídas, con correlación de 0.05 a 0.28) no gana dinero de forma estable.
3. **#1 frente al trend.** El TSMOM de 28 días en tramos tuvo mejor Sharpe que el trend en los 4 cortes (1.04 frente a 0.82 y 1.08 frente a 0.94 en 2021–2024; 0.56 frente a −0.13 y 1.32 frente a 0.56 en 2025), pero la ventana de 28 días salió de esta misma búsqueda. La regla del paper (7 días, domingo) es la única "preregistrada", y gana en ETH pero no en BTC.
4. **#3 no mejora el Sharpe.** Solo baja el drawdown y la exposición, que es lo mismo que ya hace la regla del perfil.

### 5.4 Descartadas, con números (2021–2024, Sharpe neto)

- **Reversión tras caídas** (k = 2σ, H = 3 días): BTC 0.06, ETH −0.02. Con k = 2.5 y H = 5: BTC −0.45 y ETH 0.22. El signo cambia entre parámetros y entre años.
- **Rotación BTC/ETH/USDT** (cada domingo, el activo con mejor retorno a 28 días si es > 0): 11.6 operaciones al año y Sharpe de 0.98, igual que el trend 50/50 (0.98), con correlación de 0.76 y drawdown de −59.6 % frente a −38.2 %. En 2025: 1.11 frente a 0.37. Es prometedora, pero no tiene evidencia publicada que cumpla el filtro.
- **Funding p90 como filtro del trend** (salir si el funding promedio de 7 días supera su percentil 90 de los últimos 365 días): BTC 0.82 → 0.62, ETH 0.94 → 0.93. En 2025 no se activa.

### 5.5 Funding rate: retorno a 30 días por quintil del funding promedio de 7 días (2021–2025)

| Quintil | BTC: media | BTC: P(caída > 20 %) | ETH: media | ETH: P(caída > 20 %) |
|---|---|---|---|---|
| 1 (funding bajo) | +5.0 % | 7.5 % | +3.0 % | 15.6 % |
| 3 | 0.0 % | 3.9 % | +2.5 % | 12.0 % |
| 5 (funding alto) | +1.1 % | **12.3 %** | +8.2 % | **5.0 %** |

En BTC, un funding alto sí viene con más caídas fuertes, como dicen Schmeling et al. En ETH es al revés: un funding alto acompaña a la tendencia alcista. Con una relación así de inestable entre activos, y además en solapamiento (ventanas de 30 días), no hay base para un filtro.

### 5.6 Correlación con Kronos

No la calculamos. Los pronósticos precalculados (`data/forecasts/<SYMBOL>/4h/`, de julio de 2024 a 2025) son trayectorias y no señales; convertirlos requiere la lógica del KronosAgent. Queda para el laboratorio (#13), que debe reportar la matriz de correlación entre todos los sleeves.

---

## 6. Siguientes pasos

1. Un issue por cada estrategia del ranking (milestone M4, label `agents`): #81 (TsmomAgent), #82 (MovingAverageAgent) y #83 (volatility targeting). Cada una entra en estado **research**.
2. Implementarlas como `BaseAgent` y pasarlas por el laboratorio (#13): walk-forward de 16 tramos con los parámetros elegidos dentro de cada tramo, Monte Carlo con block bootstrap y holdout 2025.
3. El laboratorio debe reportar la correlación entre sleeves. Si el TSMOM o la SMA pasan, pero con correlación > 0.8 con el trend, conviene integrarlos como votos del conjunto en lugar de darles capital propio.

Corrección aprovechada en este PR: la propuesta y el docstring del `TrendAgent` citaban "Liu, Tsyvinski y Goldstein (RFS 2021)". Según Crossref, los autores son solo Yukun Liu y Aleh Tsyvinski (DOI 10.1093/rfs/hhaa113).
