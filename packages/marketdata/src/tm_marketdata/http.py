"""GET con reintentos y backoff exponencial ante 429, 418, 5xx y errores de red."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# 429: límite de peso rebasado. 418: IP baneada temporalmente por ignorar los 429.
RETRY_STATUSES = frozenset({418, 429, 500, 502, 503, 504})
USER_AGENT = "trading_maxxing/tm-marketdata"


class BinanceHTTPError(RuntimeError):
    """La petición falló de forma definitiva (sin más reintentos)."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def default_client(timeout: float = 30.0) -> httpx.Client:
    return httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT})


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def get_with_retries(
    client: httpx.Client,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    max_retries: int = 5,
    backoff: float = 1.0,
    max_backoff: float = 60.0,
    sleep: Callable[[float], None] = time.sleep,
    on_response: Callable[[httpx.Response], None] | None = None,
) -> httpx.Response:
    """Hace un GET y reintenta lo que es transitorio.

    Devuelve la respuesta en cuanto no es reintentable (incluidos los 4xx como 404,
    que el llamador interpreta). Lanza `BinanceHTTPError` si se agotan los reintentos.
    """
    for attempt in range(max_retries + 1):
        delay = min(max_backoff, backoff * 2**attempt)
        try:
            response = client.get(url, params=params)
        except httpx.TransportError as exc:
            if attempt == max_retries:
                raise BinanceHTTPError(f"error de red en {url}: {exc}") from exc
            logger.warning("error de red en %s (%s); reintento en %.1fs", url, exc, delay)
            sleep(delay)
            continue

        if on_response is not None:
            on_response(response)
        if response.status_code not in RETRY_STATUSES:
            return response
        if attempt == max_retries:
            raise BinanceHTTPError(
                f"{response.status_code} en {url} tras {max_retries} reintentos",
                response.status_code,
            )
        wait = _retry_after(response) or delay
        logger.warning("%s en %s; reintento en %.1fs", response.status_code, url, wait)
        sleep(wait)
    raise AssertionError("inalcanzable")  # pragma: no cover
