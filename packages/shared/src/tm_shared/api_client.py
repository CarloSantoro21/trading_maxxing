"""Cliente HTTP del motor hacia la API en Cloudflare Workers (endpoints ``/internal/*``).

El motor (agents, engine) corre en la Mac y solo hace conexiones **salientes**: a Binance
Spot Testnet y a la API. Todas las piezas que antes hablaban con Supabase (PostgREST con la
llave ``service_role``) usan este cliente. El contrato está en ``docs/arquitectura/api.md``.

Variables de entorno (los mismos nombres en todos los servicios):

========================  ==============================================================
``TM_API_URL``            Base del Worker **sin** ``/internal``; p. ej.
                          ``https://tm-api.<subdominio>.workers.dev`` o, en desarrollo,
                          ``http://host.docker.internal:8787`` (``wrangler dev``).
``TM_SERVICE_TOKEN``      Secreto ``SERVICE_TOKEN`` del Worker; va en ``X-Service-Token``.
``CF_ACCESS_CLIENT_ID``   Opcional: Client ID del service token de Cloudflare Access que
                          protege ``/internal/*`` (header ``CF-Access-Client-Id``).
``CF_ACCESS_CLIENT_SECRET`` Opcional: su secreto (header ``CF-Access-Client-Secret``).
``TM_API_TIMEOUT_SECONDS`` Opcional: segundos por petición (10 por defecto).
``TM_API_RETRIES``        Opcional: reintentos ante fallas transitorias (3 por defecto).
========================  ==============================================================

Reintentos con *backoff* exponencial (0.5 s, 1 s, 2 s, ... hasta 8 s):

- siempre que la conexión ni siquiera se abrió (``ConnectError``, ``ConnectTimeout``) o la
  API respondió ``429``;
- además, en métodos idempotentes (``GET``, ``PUT``, ``PATCH``, ``DELETE``, y los ``POST``
  marcados con ``idempotent=True``), ante otros errores de red y ``5xx``. Un ``POST`` normal
  no se repite ante un ``5xx`` o un *timeout* de lectura: podría duplicar una operación.

Los errores salen como :class:`WorkerApiError` con el ``status``, el ``code`` del contrato
(``not_found``, ``stale_state``, ...) y el ``detail``.
"""

from __future__ import annotations

import logging
import os
import socket
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from tm_shared import __version__

log = logging.getLogger("tm_shared.api")

ENV_URL = "TM_API_URL"
ENV_TOKEN = "TM_SERVICE_TOKEN"
ENV_ACCESS_ID = "CF_ACCESS_CLIENT_ID"
ENV_ACCESS_SECRET = "CF_ACCESS_CLIENT_SECRET"

#: Métodos que se pueden repetir sin efectos dobles.
IDEMPOTENT = frozenset({"GET", "HEAD", "PUT", "PATCH", "DELETE"})
#: Estados que se reintentan en métodos idempotentes.
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


class WorkerApiError(Exception):
    """Falla al llamar a la API: red, Access o una respuesta de error del Worker.

    Attributes:
        status: código HTTP (``None`` si ni siquiera hubo respuesta).
        code: ``code`` del contrato (``not_found``, ``stale_state``, ``strategy_exists``...).
        detail: ``detail`` de la respuesta o el mensaje de la excepción de red.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
        detail: Any = None,
        method: str = "",
        path: str = "",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.detail = detail
        self.method = method
        self.path = path

    @property
    def not_found(self) -> bool:
        return self.status == 404


def api_configured(env: Mapping[str, str] | None = None) -> bool:
    """True si hay ``TM_API_URL`` y ``TM_SERVICE_TOKEN``."""
    env = os.environ if env is None else env
    return bool(env.get(ENV_URL) and env.get(ENV_TOKEN))


class WorkerApiClient:
    """Cliente síncrono de ``/internal/*`` (ver el módulo).

    Es seguro entre hilos (``httpx.Client`` lo es): el hilo del ExplainerAgent y el bucle
    principal comparten uno.

    Args:
        base_url: ``TM_API_URL`` (sin ``/internal``).
        token: ``TM_SERVICE_TOKEN``; se manda como ``X-Service-Token``.
        access_client_id / access_client_secret: service token de Cloudflare Access.
        timeout: segundos por petición.
        retries: reintentos ante fallas transitorias (0 = un solo intento).
        backoff: espera del primer reintento; se duplica en cada uno, hasta ``max_backoff``.
        client: ``httpx.Client`` propio (pruebas); si no, se crea uno.
        sleep: función de espera (las pruebas la sustituyen para no dormir).
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        access_client_id: str | None = None,
        access_client_secret: str | None = None,
        timeout: float = 10.0,
        retries: int = 3,
        backoff: float = 0.5,
        max_backoff: float = 8.0,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        user_agent: str | None = None,
    ) -> None:
        if not base_url:
            raise ValueError(f"falta {ENV_URL} (base del Worker, sin /internal)")
        if not token:
            raise ValueError(f"falta {ENV_TOKEN} (secreto SERVICE_TOKEN del Worker)")
        base = base_url.rstrip("/")
        if base.endswith("/internal"):
            base = base[: -len("/internal")]
        self.base_url = base
        self.timeout = timeout
        self.retries = max(0, int(retries))
        self.backoff = backoff
        self.max_backoff = max_backoff
        self._sleep = sleep
        self._headers = {
            "X-Service-Token": token,
            "Accept": "application/json",
            "User-Agent": user_agent or f"trading-maxxing-motor/{__version__}",
        }
        if access_client_id and access_client_secret:
            self._headers["CF-Access-Client-Id"] = access_client_id
            self._headers["CF-Access-Client-Secret"] = access_client_secret
        elif access_client_id or access_client_secret:
            log.warning(
                "solo viene uno de %s / %s: no se mandan headers de Access",
                ENV_ACCESS_ID,
                ENV_ACCESS_SECRET,
            )
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, **kwargs: Any) -> WorkerApiClient:
        """Arma el cliente con las variables del módulo. Lanza ``ValueError`` si faltan."""
        env = os.environ if env is None else env
        opts: dict[str, Any] = {
            "access_client_id": env.get(ENV_ACCESS_ID) or None,
            "access_client_secret": env.get(ENV_ACCESS_SECRET) or None,
        }
        if env.get("TM_API_TIMEOUT_SECONDS"):
            opts["timeout"] = float(env["TM_API_TIMEOUT_SECONDS"])
        if env.get("TM_API_RETRIES"):
            opts["retries"] = int(env["TM_API_RETRIES"])
        opts.update(kwargs)
        return cls(env.get(ENV_URL, ""), env.get(ENV_TOKEN, ""), **opts)

    @property
    def headers(self) -> dict[str, str]:
        """Headers que van en cada petición (copia)."""
        return dict(self._headers)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> WorkerApiClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # --- atajos ------------------------------------------------------------------------
    def get(self, path: str, params: Mapping[str, Any] | None = None, **kw: Any) -> Any:
        return self.request("GET", path, params=params, **kw)

    def post(self, path: str, json: Any = None, **kw: Any) -> Any:
        return self.request("POST", path, json=json, **kw)

    def patch(self, path: str, json: Any = None, **kw: Any) -> Any:
        return self.request("PATCH", path, json=json, **kw)

    def put(self, path: str, json: Any = None, **kw: Any) -> Any:
        return self.request("PUT", path, json=json, **kw)

    # --- núcleo ------------------------------------------------------------------------
    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        idempotent: bool | None = None,
        retries: int | None = None,
    ) -> Any:
        """Hace la petición y devuelve el JSON (o ``None`` si viene vacía).

        Raises:
            WorkerApiError: error de red tras los reintentos o respuesta que no es 2xx.
        """
        method = method.upper()
        url = self.base_url + (path if path.startswith("/") else "/" + path)
        safe = method in IDEMPOTENT if idempotent is None else idempotent
        attempts = 1 + (self.retries if retries is None else max(0, retries))
        clean = {k: _param(v) for k, v in (params or {}).items() if v is not None}
        last: WorkerApiError | None = None
        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            try:
                resp = self._client.request(
                    method,
                    url,
                    params=clean or None,
                    json=json,
                    headers=self._headers,
                    timeout=self.timeout,
                )
            except httpx.TransportError as exc:
                retryable = safe or isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout)
                last = WorkerApiError(
                    f"{method} {path}: no se pudo llamar a la API ({type(exc).__name__}: {exc})",
                    detail=str(exc),
                    method=method,
                    path=path,
                )
                if retryable and attempt < attempts:
                    self._wait(attempt, attempts - 1, last)
                    continue
                log.error("%s", last)
                raise last from exc
            ms = (time.monotonic() - started) * 1000
            if resp.is_success:
                log.debug("%s %s -> %s (%.0f ms)", method, path, resp.status_code, ms)
                return _body(resp)
            last = _error(method, path, resp)
            retryable = resp.status_code == 429 or (safe and resp.status_code in RETRY_STATUS)
            if retryable and attempt < attempts:
                self._wait(attempt, attempts - 1, last)
                continue
            level = logging.INFO if resp.status_code in (404, 409) else logging.WARNING
            log.log(level, "%s", last)
            raise last
        raise last or WorkerApiError(f"{method} {path}: sin intentos")  # pragma: no cover

    def _wait(self, attempt: int, total: int, error: WorkerApiError) -> None:
        delay = min(self.max_backoff, self.backoff * 2 ** (attempt - 1))
        log.warning("%s; reintento %d/%d en %.1f s", error, attempt, total, delay)
        self._sleep(delay)


def _param(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _body(resp: httpx.Response) -> Any:
    if not resp.content:
        return None
    try:
        return resp.json()
    except ValueError as exc:
        raise WorkerApiError(
            f"respuesta que no es JSON ({resp.status_code}): {resp.text[:200]}",
            status=resp.status_code,
        ) from exc


def _error(method: str, path: str, resp: httpx.Response) -> WorkerApiError:
    code: str | None = None
    detail: Any = None
    try:
        body = resp.json()
    except ValueError:
        body = None
    if isinstance(body, Mapping):
        detail = body.get("detail")
        code = body.get("code")
    if detail is None:
        detail = resp.text[:300]
    status = resp.status_code
    if status in (301, 302, 303, 307, 308) or (status in (401, 403) and body is None):
        # Cloudflare Access responde con un redirect al login o una página HTML.
        hint = " (¿Cloudflare Access? revisa CF_ACCESS_CLIENT_ID/CF_ACCESS_CLIENT_SECRET)"
    elif status == 401:
        hint = " (revisa TM_SERVICE_TOKEN)"
    else:
        hint = ""
    return WorkerApiError(
        f"{method} {path} -> {status}{f' [{code}]' if code else ''}: {detail}{hint}",
        status=status,
        code=code,
        detail=detail,
        method=method,
        path=path,
    )


class HeartbeatReporter:
    """Latido del componente en ``POST /internal/heartbeat`` (``/api/engine/status``).

    ``maybe_beat()`` se llama en cada vuelta del bucle y solo manda cada ``interval``
    segundos. Nunca lanza: si la API no responde, se registra y se sigue operando.
    """

    def __init__(
        self,
        client: WorkerApiClient,
        component: str,
        *,
        interval: float = 30.0,
        version: str | None = None,
        host: str | None = None,
        details: Callable[[], Mapping[str, Any]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], str] | None = None,
    ) -> None:
        self.client = client
        self.component = component
        self.interval = interval
        self.version = version or os.environ.get("TM_VERSION") or __version__
        self.host = host or os.environ.get("TM_HOST") or socket.gethostname()
        self._details = details
        self._clock = clock
        self.started_at = (wall_clock or _utc_now_iso)()
        self._last = float("-inf")
        self.failures = 0

    def maybe_beat(self, status: str = "ok") -> bool:
        if self._clock() - self._last < self.interval:
            return False
        return self.beat(status)

    def beat(self, status: str = "ok") -> bool:
        self._last = self._clock()
        details: dict[str, Any] = {}
        if self._details is not None:
            try:
                details = dict(self._details())
            except Exception as exc:  # el latido nunca tumba al servicio
                details = {"details_error": str(exc)}
        payload = {
            "component": self.component,
            "status": status,
            "version": self.version[:64],
            "host": self.host[:128],
            "started_at": self.started_at,
            "details": _jsonable(details),
        }
        try:
            # El latido es un upsert: repetirlo no hace daño.
            self.client.post("/internal/heartbeat", payload, idempotent=True, retries=1)
        except WorkerApiError as exc:
            self.failures += 1
            log.warning("no se pudo mandar el latido de %s: %s", self.component, exc)
            return False
        self.failures = 0
        return True


def _utc_now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _jsonable(value: Any) -> Any:
    """Reduce a tipos de JSON (``Decimal``, ``datetime``, ``Enum`` → texto)."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_jsonable(v) for v in value]
    return str(value)


__all__ = [
    "ENV_ACCESS_ID",
    "ENV_ACCESS_SECRET",
    "ENV_TOKEN",
    "ENV_URL",
    "HeartbeatReporter",
    "WorkerApiClient",
    "WorkerApiError",
    "api_configured",
]
