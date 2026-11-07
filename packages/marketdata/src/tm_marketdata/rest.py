"""Cliente de la REST pública de Binance para velas (`/api/v3/klines`)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import httpx

from tm_marketdata.http import BinanceHTTPError, default_client, get_with_retries
from tm_marketdata.timeframes import tf_ms

logger = logging.getLogger(__name__)

BASE_URL = "https://api.binance.com"
TESTNET_URL = "https://testnet.binance.vision"
KLINES_PATH = "/api/v3/klines"
PAGE_LIMIT = 1000
# Límite de peso por minuto e IP de la REST de spot; se deja margen para otros clientes.
WEIGHT_LIMIT_1M = 6000
WEIGHT_HEADER = "X-MBX-USED-WEIGHT-1M"


class BinanceRestClient:
    """Pagina `/api/v3/klines` de 1000 en 1000 respetando el peso usado por minuto."""

    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        base_url: str = BASE_URL,
        max_retries: int = 5,
        backoff: float = 1.0,
        page_limit: int = PAGE_LIMIT,
        weight_limit: int = WEIGHT_LIMIT_1M,
        weight_margin: float = 0.8,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._client = client or default_client()
        self._url = base_url.rstrip("/") + KLINES_PATH
        self._max_retries = max_retries
        self._page_limit = page_limit
        self._backoff = backoff
        self._weight_threshold = int(weight_limit * weight_margin)
        self._sleep = sleep
        self._clock = clock
        self.used_weight = 0

    def _track_weight(self, response: httpx.Response) -> None:
        value = response.headers.get(WEIGHT_HEADER)
        if value is not None and value.isdigit():
            self.used_weight = int(value)

    def _throttle(self) -> None:
        """Si el peso usado se acerca al límite, espera a que empiece el siguiente minuto."""
        if self.used_weight < self._weight_threshold:
            return
        wait = 60 - (self._clock() % 60) + 1
        logger.info("peso usado %s; espero %.1fs al siguiente minuto", self.used_weight, wait)
        self._sleep(wait)
        self.used_weight = 0

    def klines(self, symbol: str, tf: str, start_ms: int, end_ms: int) -> list[list]:
        """Velas crudas con `start_ms <= open_time < end_ms` (incluye la vela abierta)."""
        step = tf_ms(tf)
        rows: list[list] = []
        cursor = start_ms
        while cursor < end_ms:
            self._throttle()
            params = {
                "symbol": symbol,
                "interval": tf,
                "startTime": cursor,
                "endTime": end_ms - 1,
                "limit": self._page_limit,
            }
            response = get_with_retries(
                self._client,
                self._url,
                params=params,
                max_retries=self._max_retries,
                backoff=self._backoff,
                sleep=self._sleep,
                on_response=self._track_weight,
            )
            if response.status_code != 200:
                raise BinanceHTTPError(
                    f"Binance respondió {response.status_code}: {response.text[:200]}",
                    response.status_code,
                )
            batch = response.json()
            if not batch:
                break
            rows.extend(batch)
            cursor = int(batch[-1][0]) + step
            if len(batch) < self._page_limit:
                break
        return rows
