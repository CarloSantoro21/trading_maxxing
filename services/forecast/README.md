# forecast

Microservicio de pronóstico: envuelve Kronos-small (`KronosForecastService`) y expone
`POST /predict` y `POST /predict/batch`, que devuelven N trayectorias muestreadas
(`samples × pred_len` velas OHLCV). Las consumen el KronosAgent y la SimulationGate.

```bash
uv run --package tm-forecast uvicorn tm_forecast.main:app --port 8001
```

La primera vez descarga los pesos de Hugging Face (~110 MB, quedan en `~/.cache/huggingface`).

## Configuración

| Variable | Default | Uso |
|---|---|---|
| `FORECAST_MODEL` | `NeoQuasar/Kronos-small` | Modelo de Hugging Face |
| `FORECAST_TOKENIZER` | `NeoQuasar/Kronos-Tokenizer-base` | Tokenizer de Hugging Face |
| `FORECAST_DEVICE` | automático | `mps`, `cuda`, `cuda:N` o `cpu`. Sin valor: `mps` → `cuda` → `cpu` |
| `FORECAST_PRELOAD` | `1` | `0` para no cargar el modelo en el arranque (se carga en la primera petición) |
| `FORECAST_MAX_BATCH` | `512` | Trayectorias máximas por llamada al modelo (el resto se parte en lotes) |

El modelo se carga una sola vez, en el `lifespan` de FastAPI. `GET /health` informa el
dispositivo, el modelo y si ya está cargado (`model_loaded`).

## API

`POST /predict`

```json
{
  "candles": [
    {"timestamp": "2026-11-07T19:15:00Z", "open": 75810.1, "high": 75850.0, "low": 75790.2, "close": 75799.9, "volume": 120.5}
  ],
  "pred_len": 24,
  "samples": 100,
  "seed": 42
}
```

- `candles`: de 32 a 512 velas (el contexto máximo de Kronos-small), todas con `timestamp`,
  `open`, `high`, `low`, `close` y `volume`. Si falta una columna, sobran o faltan velas, o hay
  timestamps repetidos: **422**.
- `pred_len` (1–256, default 24), `samples` (1–500, default 100), `seed` opcional (misma semilla
  y mismo dispositivo ⇒ mismas trayectorias), `temperature` (default 1.0) y `top_p` (default 0.9).
- Respuesta: `timestamps` (las `pred_len` velas futuras, con la frecuencia mediana de la
  historia) y `trajectories`, una matriz `samples × pred_len` de velas.

`POST /predict/batch` recibe `series: [{candles: [...]}, ...]` (hasta 16, todas con la misma
cantidad de velas, requisito de `predict_batch` de Kronos) y los mismos parámetros; devuelve
`results` con una respuesta por serie.

### Cómo se obtienen las trayectorias

`KronosPredictor.predict()` y `predict_batch()` **promedian** internamente las `sample_count`
muestras, así que no sirven tal cual para obtener N trayectorias. El servicio replica la serie
`samples` veces dentro de un solo `predict_batch` con `sample_count=1`: es exactamente lo que
Kronos hace por dentro (repite el lote), pero sin promediar, y no requiere modificar su código.

## Latencia medida

Apple M5 (16 GB), macOS, ejecución nativa (fuera de Docker), PyTorch 2.15.0, Kronos-small.
400 velas reales de BTCUSDT 15m (Binance, 3 al 7 de nov. de 2026) → 24 velas. Mediana de 3
corridas después de un calentamiento, torch con sus 4 hilos por defecto; la carga del modelo (≈1.5 s, ya en caché) no se cuenta.

| Trayectorias | `mps` | `cpu` |
|---|---|---|
| 1 | 0.57 s | 1.1 s |
| 9 | — | 9.0 s |
| 10 | — | 10.2 s |
| 25 | 16.3 s | 28 s |
| 50 | 31.8 s | — |
| 100 | **64.9 s** | **132 s** (113–146 s) |

El costo crece **linealmente** con las trayectorias: Kronos no usa caché de KV, así que en
cada uno de los 24 pasos autorregresivos vuelve a procesar las ~400 velas de contexto para
cada trayectoria (el perfil en CPU es ~80 % `mm`/`addmm`/atención). Agrupar en lotes no
amortiza nada.

**El criterio del issue (< 10 s en CPU con 100 trayectorias) no se cumple.** Con 400 velas → 24:

- CPU: el máximo viable bajo 10 s es **9 trayectorias** (9.0 s medido; 10 ya pasa, 10.2 s). 100 tardan ~2 min.
- MPS: ~15 trayectorias en 10 s (extrapolado de ~0.65 s por trayectoria); 100 tardan ~65 s, la mitad que en CPU.

Implicaciones para el resto del sistema:

- En producción (velas de 15 min) 100 trayectorias en ~65 s con `mps` caben de sobra en el
  intervalo, así que conviene correr forecast nativo en la Mac.
- Para el backtest con Kronos (una predicción por vela) hay que precalcular y guardar en caché
  las trayectorias, o usar menos trayectorias y contexto más corto; el tiempo es
  aproximadamente proporcional a `samples × contexto × pred_len`.

## Corte de los datos de preentrenamiento

El paper de Kronos (Shi et al., arXiv [2508.02739](https://arxiv.org/abs/2508.02739) v1,
apéndice D, *Forecasting Task Setup*) dice: *"The pre-training data for Kronos extends up to
June 2024. Consequently, our test period for all tasks begins in July 2024"*. El README del
repo y la ficha de Hugging Face no dan fecha. El corpus incluye cripto (el apéndice C menciona
que se sobremuestrean cripto, futuros y forex).

Consecuencia: cualquier evaluación de Kronos en el walk-forward debe usar periodos
**a partir de julio de 2024**. Las ventanas de 2020 a junio de 2024 ya las vio el modelo, así
que un backtest de Kronos ahí está contaminado.

## Pruebas

Las pruebas (`tests/`) usan un predictor simulado: no descargan pesos ni usan red.

```bash
uv run pytest services/forecast --cov
```

## Código de Kronos

Kronos no está publicado en PyPI, así que su código está incorporado en
[`src/tm_forecast/kronos`](src/tm_forecast/kronos/README.md) (licencia MIT, con el commit de
origen). En Linux (CI y Docker) torch se instala desde el índice de CPU de PyTorch, sin CUDA;
en macOS desde PyPI, con soporte de MPS.
