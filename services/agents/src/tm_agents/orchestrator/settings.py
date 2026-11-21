"""Interruptores en caliente del motor: noticias y ExplainerAgent, sin redeploy.

El orquestador consulta un :class:`SettingsProvider` con ``get_bool("news_enabled")`` y
``get_bool("explainer_enabled")`` en cada ciclo; los proveedores remotos guardan la respuesta
en caché ``SETTINGS_REFRESH_SECONDS`` (300 s por defecto), así que un cambio del admin se
nota en, a lo más, unos minutos.

Proveedores (``SETTINGS_SOURCE``):

- ``env`` (por defecto): lee ``NEWS_ENABLED`` y ``EXPLAINER_ENABLED`` del entorno. Ambos son
  ``false`` si no se definen: **por defecto no cuesta nada**.
- ``worker-api``: **hook** para el interruptor del admin. Hace
  ``GET {WORKER_API_URL}/internal/settings`` con ``Authorization: Bearer
  {WORKER_SERVICE_TOKEN}`` contra la API en Cloudflare Workers, que lee la tabla ``settings``
  de D1. El endpoint lo implementa la API (otro ticket); aquí solo se consume. Si la API no
  responde, se usan los últimos valores conocidos y, si nunca respondió, los del entorno.

Formato esperado de ``/internal/settings`` (cualquiera de los tres):

.. code-block:: json

    {"news_enabled": true, "explainer_enabled": false}
    {"settings": {"news_enabled": true}}
    [{"key": "news_enabled", "value": "true"}]
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol, runtime_checkable

import httpx

log = logging.getLogger(__name__)

#: Interruptores conocidos y la variable de entorno que da su valor por defecto.
ENV_KEYS: Mapping[str, str] = {
    "news_enabled": "NEWS_ENABLED",
    "explainer_enabled": "EXPLAINER_ENABLED",
}
#: Todo apagado si nadie dice lo contrario.
DEFAULTS: Mapping[str, bool] = {"news_enabled": False, "explainer_enabled": False}

_TRUE = {"1", "true", "yes", "on", "si", "sí"}
_FALSE = {"0", "false", "no", "off", ""}


def parse_bool(value: Any, default: bool = False) -> bool:
    """``"true"``/``"1"``/``True`` → ``True``; ``"false"``/``"0"``/``""`` → ``False``."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    if isinstance(value, str):
        v = value.strip().lower()
        if v in _TRUE:
            return True
        if v in _FALSE:
            return False
    return default


@runtime_checkable
class SettingsProvider(Protocol):
    """De dónde salen los interruptores del motor."""

    def get_bool(self, key: str, default: bool | None = None) -> bool: ...


class EnvSettingsProvider:
    """Interruptores desde el entorno (``news_enabled`` → ``NEWS_ENABLED``)."""

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env = os.environ if env is None else env

    def get_bool(self, key: str, default: bool | None = None) -> bool:
        fallback = DEFAULTS.get(key, False) if default is None else default
        raw = self._env.get(ENV_KEYS.get(key, key.upper()))
        if raw is None:
            return fallback
        return parse_bool(raw, fallback)


class StaticSettingsProvider:
    """Valores fijos (pruebas y backtest)."""

    def __init__(self, values: Mapping[str, Any] | None = None) -> None:
        self.values = dict(values or {})

    def get_bool(self, key: str, default: bool | None = None) -> bool:
        fallback = DEFAULTS.get(key, False) if default is None else default
        return parse_bool(self.values[key], fallback) if key in self.values else fallback


class WorkerApiSettingsProvider:
    """Hook hacia ``GET /internal/settings`` de la API en Workers (ver el módulo).

    Fail-safe: si la API falla se conservan los últimos valores conocidos; si nunca ha
    respondido se usa ``fallback`` (el entorno).
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        fallback: SettingsProvider | None = None,
        refresh_seconds: float = 300.0,
        timeout: float = 5.0,
        http_client: httpx.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not base_url:
            raise ValueError("WorkerApiSettingsProvider necesita WORKER_API_URL")
        if not token:
            raise ValueError("WorkerApiSettingsProvider necesita WORKER_SERVICE_TOKEN")
        self.url = f"{base_url.rstrip('/')}/internal/settings"
        self._headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        self.fallback = fallback or EnvSettingsProvider()
        self.refresh_seconds = refresh_seconds
        self._timeout = timeout
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(timeout=timeout)
        self._clock = clock
        self._values: dict[str, Any] | None = None
        self._fetched_at = float("-inf")

    def fetch(self) -> dict[str, Any]:
        """Lee ``/internal/settings``. Lanza ``httpx.HTTPError`` o ``ValueError``."""
        resp = self._http.get(self.url, headers=self._headers, timeout=self._timeout)
        resp.raise_for_status()
        return _normalize(resp.json())

    def _refresh(self) -> None:
        now = self._clock()
        if now - self._fetched_at < self.refresh_seconds:
            return
        self._fetched_at = now
        try:
            self._values = self.fetch()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            log.warning("no se pudo leer /internal/settings; se usan los últimos valores (%s)", exc)

    def get_bool(self, key: str, default: bool | None = None) -> bool:
        self._refresh()
        if self._values is not None and key in self._values:
            fallback = self.fallback.get_bool(key, default)
            return parse_bool(self._values[key], fallback)
        return self.fallback.get_bool(key, default)

    def close(self) -> None:
        if self._owns_client:
            self._http.close()


def _normalize(body: Any) -> dict[str, Any]:
    if isinstance(body, Mapping) and isinstance(body.get("settings"), Mapping | list):
        body = body["settings"]
    if isinstance(body, list):
        return {str(row["key"]): row.get("value") for row in body if isinstance(row, Mapping)}
    if isinstance(body, Mapping):
        return {str(k): v for k, v in body.items()}
    raise ValueError(f"formato inesperado de /internal/settings: {type(body).__name__}")


def settings_from_env(env: Mapping[str, str] | None = None) -> SettingsProvider:
    """``SETTINGS_SOURCE=env`` (por defecto) o ``worker-api`` (ver el módulo)."""
    env = os.environ if env is None else env
    source = (env.get("SETTINGS_SOURCE") or "env").strip().lower()
    base = EnvSettingsProvider(env)
    if source == "env":
        return base
    if source in ("worker-api", "worker_api", "api"):
        return WorkerApiSettingsProvider(
            env.get("WORKER_API_URL", ""),
            env.get("WORKER_SERVICE_TOKEN", ""),
            fallback=base,
            refresh_seconds=float(env.get("SETTINGS_REFRESH_SECONDS") or 300),
        )
    raise ValueError(f"SETTINGS_SOURCE desconocido: '{source}' (soportados: env, worker-api)")


__all__ = [
    "DEFAULTS",
    "ENV_KEYS",
    "EnvSettingsProvider",
    "SettingsProvider",
    "StaticSettingsProvider",
    "WorkerApiSettingsProvider",
    "parse_bool",
    "settings_from_env",
]
