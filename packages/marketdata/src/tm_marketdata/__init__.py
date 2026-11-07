"""Velas de Binance para trading_maxxing: histórico en Parquet, REST, cache y streaming."""

from tm_marketdata.cache import BarCache, NullCache, RedisCache, cache_from_env
from tm_marketdata.rest import BinanceRestClient
from tm_marketdata.service import MarketDataService
from tm_marketdata.storage import ParquetStore
from tm_marketdata.stream import Bar, stream_bars
from tm_marketdata.timeframes import TIMEFRAMES

__version__ = "0.1.0"

__all__ = [
    "TIMEFRAMES",
    "Bar",
    "BarCache",
    "BinanceRestClient",
    "MarketDataService",
    "NullCache",
    "ParquetStore",
    "RedisCache",
    "cache_from_env",
    "stream_bars",
]
