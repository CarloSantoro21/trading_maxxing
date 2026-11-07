# tm-marketdata

Velas de Binance para BTC/USDT y ETH/USDT: descarga histórica a Parquet, lectura local, REST pública con cache opcional en Redis y streaming de velas cerradas. Lo usan `agents`, `engine` y `api` (`MarketDataService` en la propuesta, sección 3).

## Uso

```python
from tm_marketdata import MarketDataService

svc = MarketDataService(data_dir="data/bars")  # o $TM_DATA_DIR
df = svc.get_bars("BTC/USDT", "15m", "2024-01-01", "2024-04-01")
# índice open_time (UTC); columnas open, high, low, close, volume (float64)
df = svc.get_bars("ETHUSDT", "1d", "2020-01-01", include_extra=True)  # + quote_volume, trades
```

- Timeframes: `1m`, `15m`, `30m` y `1d`.
- Rango: `start <= open_time < end`. Sin zona horaria se asume UTC. `end=None` llega hasta la última vela cerrada.
- **Solo velas cerradas**: `end` se recorta a la última vela que ya cerró (`open_time + tf <= ahora`), así que nunca sale la vela en curso (sin look-ahead).
- **Disco primero**: lo que está en Parquet se lee de disco; solo los tramos que faltan al inicio o al final del rango se piden a la REST (`/api/v3/klines`, de 1000 en 1000). Los huecos internos no se rellenan porque son huecos reales de Binance.
- La REST respeta el peso por minuto (`X-MBX-USED-WEIGHT-1M`, espera al siguiente minuto al llegar al 80 % de 6000) y reintenta con backoff exponencial ante 429, 418, 5xx y errores de red (respeta `Retry-After`).

### Cache en Redis (opcional)

Con `REDIS_URL` definida, las respuestas que tocan la REST se guardan en Redis (serializadas en Parquet) con un TTL por timeframe: 30 s (1m), 5 min (15m), 10 min (30m) y 1 h (1d). Sin `REDIS_URL` se usa `NullCache` y todo funciona igual. Si Redis falla, se registra y se trata como un fallo de cache. Para otro backend basta implementar la interfaz `BarCache` (`get`/`set`).

### Streaming

```python
async for bar in svc.stream_bars("BTCUSDT", "1m"):
    ...  # Bar(symbol, tf, open_time, open, high, low, close, volume, quote_volume, trades)
```

Usa `wss://stream.binance.com:9443/ws/<symbol>@kline_<tf>`, emite solo velas cerradas (`k.x == true`), se reconecta solo con backoff (Binance corta cada conexión a las 24 h) y descarta velas repetidas tras reconectar.

## Descarga histórica

```bash
uv run --package tm-marketdata python -m tm_marketdata download \
  --symbols BTCUSDT ETHUSDT --tf 1m 15m 30m 1d \
  --start 2020-01-01 --end 2025-12-31 --data-dir data/bars

# velas, rango, tamaño y huecos de lo que ya está en disco
uv run --package tm-marketdata python -m tm_marketdata report --data-dir data/bars
```

- Baja los zips mensuales de `data.binance.vision` (mucho más rápido que la REST), valida el SHA-256 contra el `.CHECKSUM` cuando existe y normaliza las columnas. Desde 2025 algunos archivos traen los timestamps en microsegundos: se detecta fila por fila.
- Es reanudable: cada Parquet anual anota en sus metadatos los meses que contiene y esos no se vuelven a bajar. Los meses de un año a medias se guardan en `.parts/` hasta consolidarse. Un mes que todavía no está publicado (404) se reporta y se reintenta en la siguiente corrida.
- `--workers` controla las descargas en paralelo (por defecto 4).
- Sale con código 1 si algún mes falló (checksum o HTTP), para poder usarlo en scripts.

## Estructura de archivos

```
data/bars/
  BTCUSDT/
    1m/2020.parquet … 2025.parquet
    15m/  30m/  1d/
  ETHUSDT/
    …
```

Cada archivo tiene el índice `open_time` (UTC) y las columnas `open, high, low, close, volume, quote_volume` (float64) y `trades` (int64), comprimido con zstd. `quote_volume` es el "amount" que usa Kronos. `data/` y `*.parquet` están en `.gitignore`.

## Código

| Módulo | Qué hace |
|---|---|
| `service.py` | `MarketDataService.get_bars` y `stream_bars` |
| `storage.py` | `ParquetStore`: un Parquet por símbolo, timeframe y año |
| `rest.py` | Cliente de `/api/v3/klines` con paginación y control de peso |
| `http.py` | GET con reintentos y backoff |
| `cache.py` | `BarCache`, `NullCache`, `RedisCache` y `cache_from_env` |
| `stream.py` | Websocket de velas cerradas con reconexión |
| `vision.py` | Descargador de data.binance.vision |
| `quality.py` | Conteo de velas y detección de huecos |
| `cli.py` | `python -m tm_marketdata download|report` |

## Pruebas

Sin red: `respx` para la REST y data.binance.vision, `fakeredis` para el cache y un servidor websocket local para el streaming. Las respuestas grabadas están en `tests/fixtures/` (dos páginas reales de `/api/v3/klines` y el zip real `BTCUSDT-1d-2020-01.zip` con su `.CHECKSUM`).

```bash
uv run pytest packages/marketdata --cov=packages/marketdata/src
```

## Fuentes

- REST: <https://api.binance.com/api/v3/klines> ([docs](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints#klinecandlestick-data))
- Websocket: `wss://stream.binance.com:9443/ws/<symbol>@kline_<tf>` ([docs](https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams#klinecandlestick-streams-for-utc))
- Histórico: <https://data.binance.vision> (`data/spot/monthly/klines/{SYMBOL}/{tf}/`), [binance-public-data](https://github.com/binance/binance-public-data)
