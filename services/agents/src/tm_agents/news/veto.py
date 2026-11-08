"""Almacén de vetos con expiración: Redis (`REDIS_URL`) con fallback en memoria.

Claves en Redis: `tm:news:veto:<scope>` con `scope` = ticker (`BTC`) o `*` (global), valor
JSON del `Veto` y TTL igual al tiempo que le queda. Un veto nuevo solo reemplaza al existente
si expira después, así reprocesar la misma noticia no alarga el bloqueo.

Si Redis falla, el veto se guarda y se consulta en memoria (se registra la falla). Así un
veto activo no se pierde por una caída momentánea de Redis dentro del mismo proceso.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

import redis
from pydantic import ValidationError

from tm_agents.news.models import Veto

logger = logging.getLogger(__name__)

KEY_PREFIX = "tm:news:veto"

Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


@runtime_checkable
class VetoStore(Protocol):
    def put(self, veto: Veto) -> bool:
        """Guarda el veto si expira después del vigente. Devuelve si quedó guardado."""
        ...

    def get(self, scope: str) -> Veto | None: ...

    def active(self) -> list[Veto]: ...


class InMemoryVetoStore:
    def __init__(self, clock: Clock = _utcnow) -> None:
        self._clock = clock
        self._vetoes: dict[str, Veto] = {}

    def put(self, veto: Veto) -> bool:
        now = self._clock()
        if veto.expires_at <= now:
            return False
        current = self.get(veto.scope)
        if current is not None and current.expires_at >= veto.expires_at:
            return False
        self._vetoes[veto.scope] = veto
        return True

    def get(self, scope: str) -> Veto | None:
        veto = self._vetoes.get(scope)
        if veto is None:
            return None
        if veto.expires_at <= self._clock():
            del self._vetoes[scope]
            return None
        return veto

    def active(self) -> list[Veto]:
        return [v for scope in list(self._vetoes) if (v := self.get(scope)) is not None]


class RedisVetoStore:
    """Vetos en Redis con `SET ... EX`. Ante `RedisError` usa el almacén en memoria."""

    def __init__(self, client: redis.Redis, clock: Clock = _utcnow) -> None:
        self._client = client
        self._clock = clock
        self._fallback = InMemoryVetoStore(clock)

    @staticmethod
    def key(scope: str) -> str:
        return f"{KEY_PREFIX}:{scope}"

    def put(self, veto: Veto) -> bool:
        ttl = veto.ttl_seconds(self._clock())
        if ttl <= 0:
            return False
        try:
            current = self._read(self.key(veto.scope))
            if current is not None and current.expires_at >= veto.expires_at:
                return False
            self._client.set(self.key(veto.scope), veto.model_dump_json(), ex=ttl)
            return True
        except redis.RedisError as exc:
            logger.warning("Redis no disponible al guardar veto %s: %s", veto.scope, exc)
            return self._fallback.put(veto)

    def get(self, scope: str) -> Veto | None:
        try:
            veto = self._read(self.key(scope))
        except redis.RedisError as exc:
            logger.warning("Redis no disponible al leer veto %s: %s", scope, exc)
            return self._fallback.get(scope)
        return veto or self._fallback.get(scope)

    def active(self) -> list[Veto]:
        try:
            keys = sorted(self._client.scan_iter(match=f"{KEY_PREFIX}:*"))
            found = {v.scope: v for k in keys if (v := self._read(k)) is not None}
        except redis.RedisError as exc:
            logger.warning("Redis no disponible al listar vetos: %s", exc)
            found = {}
        for v in self._fallback.active():
            found.setdefault(v.scope, v)
        return list(found.values())

    def _read(self, key: str | bytes) -> Veto | None:
        raw = self._client.get(key)
        if raw is None:
            return None
        try:
            veto = Veto.model_validate_json(raw)
        except ValidationError:
            logger.warning("veto corrupto en Redis (%r), se ignora", key)
            return None
        return veto if veto.expires_at > self._clock() else None


def veto_store_from_env(
    env: Mapping[str, str] | None = None, clock: Clock = _utcnow
) -> RedisVetoStore | InMemoryVetoStore:
    """`RedisVetoStore` si hay `REDIS_URL`; si no, vetos solo en memoria del proceso."""
    env = os.environ if env is None else env
    url = env.get("REDIS_URL")
    if not url:
        logger.info("sin REDIS_URL: los vetos de noticias se guardan solo en memoria")
        return InMemoryVetoStore(clock)
    return RedisVetoStore(redis.Redis.from_url(url), clock)
