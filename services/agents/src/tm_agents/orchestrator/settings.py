"""Interruptores en caliente del motor: noticias y ExplainerAgent, sin redeploy.

El orquestador consulta un :class:`SettingsProvider` con ``get_bool("news_enabled")`` y
``get_bool("explainer_enabled")`` en cada ciclo; los proveedores remotos guardan la respuesta
en caché ``SETTINGS_REFRESH_SECONDS`` (300 s por defecto), así que un cambio del admin se
nota en, a lo más, unos minutos.

Proveedores (``SETTINGS_SOURCE``):

- ``env``: lee ``NEWS_ENABLED`` y ``EXPLAINER_ENABLED`` del entorno. Ambos son ``false`` si no
  se definen: **por defecto no cuesta nada**.
- ``worker-api``: el interruptor del admin (``PUT /api/admin/settings``). Hace
  ``GET {TM_API_URL}/internal/settings`` con el cliente común
  (:class:`~tm_shared.api_client.WorkerApiClient`: ``X-Service-Token: {TM_SERVICE_TOKEN}`` y,
  si existen, ``CF-Access-Client-Id``/``CF-Access-Client-Secret``), que lee la tabla
  ``settings`` de D1. Si la API no responde, se usan los últimos valores conocidos y, si
  nunca respondió, los del entorno.
- sin ``SETTINGS_SOURCE``: ``worker-api`` si hay ``TM_API_URL`` y ``TM_SERVICE_TOKEN``; si no,
  ``env``.

Respuesta de ``/internal/settings`` (contrato de ``docs/arquitectura/api.md``):

.. code-block:: json

    {"news_enabled": false, "explainer_enabled": false}
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol, runtime_checkable

from tm_shared.api_client import WorkerApiClient, WorkerApiError, api_configured

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
    """Interruptores desde ``GET /internal/settings`` de la API en Workers (ver el módulo).

    Fail-safe: si la API falla se conservan los últimos valores conocidos; si nunca ha
    respondido se usa ``fallback`` (el entorno).
    """

    path = "/internal/settings"

    def __init__(
        self,
        client: WorkerApiClient,
        *,
        fallback: SettingsProvider | None = None,
        refresh_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.fallback = fallback or EnvSettingsProvider()
        self.refresh_seconds = refresh_seconds
        self._clock = clock
        self._values: dict[str, Any] | None = None
        self._fetched_at = float("-inf")

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, **kwargs: Any
    ) -> WorkerApiSettingsProvider:
        """``TM_API_URL`` y ``TM_SERVICE_TOKEN`` (``ValueError`` si faltan)."""
        env = os.environ if env is None else env
        kwargs.setdefault("fallback", EnvSettingsProvider(env))
        kwargs.setdefault("refresh_seconds", float(env.get("SETTINGS_REFRESH_SECONDS") or 300))
        # Pocos reintentos: un ciclo del orquestador no debe colgarse por los ajustes.
        return cls(WorkerApiClient.from_env(env, retries=1), **kwargs)

    def fetch(self) -> dict[str, Any]:
        """Lee ``/internal/settings``. Lanza ``WorkerApiError`` o ``ValueError``."""
        body = self.client.get(self.path, retries=0)
        if not isinstance(body, Mapping):
            raise ValueError(f"formato inesperado de /internal/settings: {type(body).__name__}")
        return {str(k): v for k, v in body.items()}

    def _refresh(self) -> None:
        now = self._clock()
        if now - self._fetched_at < self.refresh_seconds:
            return
        self._fetched_at = now
        try:
            self._values = self.fetch()
        except (WorkerApiError, ValueError) as exc:
            log.warning("no se pudo leer /internal/settings; se usan los últimos valores (%s)", exc)

    def get_bool(self, key: str, default: bool | None = None) -> bool:
        self._refresh()
        if self._values is not None and key in self._values:
            fallback = self.fallback.get_bool(key, default)
            return parse_bool(self._values[key], fallback)
        return self.fallback.get_bool(key, default)

    def close(self) -> None:
        self.client.close()


def settings_from_env(env: Mapping[str, str] | None = None) -> SettingsProvider:
    """``SETTINGS_SOURCE=env`` o ``worker-api``; sin definir, ``worker-api`` si la API está
    configurada (ver el módulo)."""
    env = os.environ if env is None else env
    default = "worker-api" if api_configured(env) else "env"
    source = (env.get("SETTINGS_SOURCE") or default).strip().lower()
    base = EnvSettingsProvider(env)
    if source == "env":
        return base
    if source in ("worker-api", "worker_api", "api"):
        return WorkerApiSettingsProvider.from_env(env, fallback=base)
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
