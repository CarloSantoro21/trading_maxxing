"""Cache opcional de respuestas recientes. Con `REDIS_URL` usa Redis; sin ella, no cachea."""

from __future__ import annotations

import io
import logging
import os
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

import pandas as pd
import redis

logger = logging.getLogger(__name__)

KEY_PREFIX = "tm:bars"


@runtime_checkable
class BarCache(Protocol):
    """Interfaz del cache de velas."""

    def get(self, key: str) -> pd.DataFrame | None: ...

    def set(self, key: str, df: pd.DataFrame, ttl_seconds: int) -> None: ...


class NullCache:
    """No guarda nada: el servicio funciona igual sin Redis."""

    def get(self, key: str) -> pd.DataFrame | None:
        return None

    def set(self, key: str, df: pd.DataFrame, ttl_seconds: int) -> None:
        return None


class RedisCache:
    """Guarda cada DataFrame serializado en Parquet con un TTL.

    Un error de Redis nunca rompe la consulta: se registra y se trata como un fallo de cache.
    """

    def __init__(self, client) -> None:
        self._client = client

    def get(self, key: str) -> pd.DataFrame | None:
        try:
            data = self._client.get(key)
        except redis.RedisError as exc:
            logger.warning("Redis no disponible al leer %s: %s", key, exc)
            return None
        if data is None:
            return None
        return pd.read_parquet(io.BytesIO(data), engine="pyarrow")

    def set(self, key: str, df: pd.DataFrame, ttl_seconds: int) -> None:
        buffer = io.BytesIO()
        df.to_parquet(buffer, engine="pyarrow")
        try:
            self._client.set(key, buffer.getvalue(), ex=ttl_seconds)
        except redis.RedisError as exc:
            logger.warning("Redis no disponible al escribir %s: %s", key, exc)


def bars_key(symbol: str, tf: str, start_ms: int, end_ms: int, extra: bool = False) -> str:
    suffix = ":extra" if extra else ""
    return f"{KEY_PREFIX}:{symbol}:{tf}:{start_ms}:{end_ms}{suffix}"


def cache_from_env(env: Mapping[str, str] | None = None) -> BarCache:
    """`RedisCache` si `REDIS_URL` está definida; `NullCache` si no."""
    env = os.environ if env is None else env
    url = env.get("REDIS_URL")
    if not url:
        return NullCache()
    return RedisCache(redis.Redis.from_url(url))
