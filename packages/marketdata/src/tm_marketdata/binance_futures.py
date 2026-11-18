"""Cliente de Binance USD-M (futuros) para funding rate y open interest.

Lee solo endpoints públicos (no requiere API key). Spot Testnet no tiene derivados: si
``BINANCE_SPOT_TESTNET_ONLY=true``, cada método lanza ``DerivativesUnavailable`` y el
StrategyAuditorAgent (#108) marca la Regla 3 como inconclusive.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

BINANCE_FUTURES_BASE_URL = "https://fapi.binance.com"
USER_AGENT = "trading_maxxing/tm-marketdata"

PREMIUM_INDEX_PATH = "/fapi/v1/premiumIndex"
FUNDING_RATE_PATH = "/fapi/v1/fundingRate"
OPEN_INTEREST_PATH = "/fapi/v1/openInterest"
OPEN_INTEREST_HIST_PATH = "/futures/data/openInterestHist"

# 408: timeout del servidor. 429: rate limit. 5xx: errores transitorios.
RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


class DerivativesUnavailable(RuntimeError):
    """Los derivados no están disponibles (p.ej. ``BINANCE_SPOT_TESTNET_ONLY=true``)."""


class BinanceFuturesError(RuntimeError):
    """La petición a Binance USD-M falló definitivamente."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class FundingRate(BaseModel):
    symbol: str
    funding_rate: Decimal
    funding_time: datetime
    mark_price: Decimal | None = None


class OpenInterest(BaseModel):
    symbol: str
    open_interest: Decimal
    notional_usd: Decimal | None = None
    ts: datetime


def _testnet_only(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    value = (env.get("BINANCE_SPOT_TESTNET_ONLY") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _to_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BinanceFuturesError(f"valor decimal inválido: {value!r}") from exc


def _to_optional_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return _to_decimal(value)


def _ts_from_ms(value: Any) -> datetime:
    try:
        ms = int(value)
    except (TypeError, ValueError) as exc:
        raise BinanceFuturesError(f"timestamp inválido: {value!r}") from exc
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def _parse_iso_ts(value: Any) -> datetime:
    """Convierte el ``timestamp`` ISO que trae ``openInterestHist`` a ``datetime`` UTC."""
    if isinstance(value, int | float):
        return _ts_from_ms(value)
    if isinstance(value, str):
        try:
            return _ts_from_ms(value)
        except BinanceFuturesError:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise BinanceFuturesError(f"timestamp inválido: {value!r}") from exc
    raise BinanceFuturesError(f"timestamp inválido: {value!r}")


class BinanceFuturesClient:
    """Cliente mínimo para funding rate y open interest de Binance USD-M."""

    def __init__(
        self,
        base_url: str = BINANCE_FUTURES_BASE_URL,
        *,
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._sleep = sleep
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(
            timeout=timeout, headers={"User-Agent": USER_AGENT}
        )
        self._testnet_only = _testnet_only(env)

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    # ---- endpoints públicos ---------------------------------------------------

    def get_funding_rate(self, symbol: str) -> FundingRate:
        """GET ``/fapi/v1/premiumIndex?symbol=...`` (último funding + mark price)."""
        self._guard()
        raw = self._get(PREMIUM_INDEX_PATH, params={"symbol": symbol.upper()})
        if not isinstance(raw, Mapping):
            raise BinanceFuturesError(f"respuesta inesperada de premiumIndex: {raw!r}")
        try:
            return FundingRate(
                symbol=str(raw["symbol"]),
                funding_rate=_to_decimal(raw["lastFundingRate"]),
                funding_time=_ts_from_ms(raw["nextFundingTime"]),
                mark_price=_to_optional_decimal(raw.get("markPrice")),
            )
        except KeyError as exc:
            raise BinanceFuturesError(f"premiumIndex sin campo {exc}: {raw!r}") from exc

    def get_funding_history(self, symbol: str, limit: int = 30) -> list[FundingRate]:
        """GET ``/fapi/v1/fundingRate?symbol=...&limit=...`` (histórico ordenado)."""
        self._guard()
        raw = self._get(
            FUNDING_RATE_PATH,
            params={"symbol": symbol.upper(), "limit": int(limit)},
        )
        if not isinstance(raw, list):
            raise BinanceFuturesError(f"respuesta inesperada de fundingRate: {raw!r}")
        result: list[FundingRate] = []
        for row in raw:
            if not isinstance(row, Mapping):
                continue
            try:
                result.append(
                    FundingRate(
                        symbol=str(row["symbol"]),
                        funding_rate=_to_decimal(row["fundingRate"]),
                        funding_time=_ts_from_ms(row["fundingTime"]),
                        mark_price=_to_optional_decimal(row.get("markPrice")),
                    )
                )
            except KeyError as exc:
                logger.warning("fila de fundingRate ignorada (falta %s): %r", exc, row)
        return result

    def get_open_interest(self, symbol: str) -> OpenInterest:
        """GET ``/fapi/v1/openInterest?symbol=...`` (snapshot del contrato)."""
        self._guard()
        raw = self._get(OPEN_INTEREST_PATH, params={"symbol": symbol.upper()})
        if not isinstance(raw, Mapping):
            raise BinanceFuturesError(f"respuesta inesperada de openInterest: {raw!r}")
        try:
            return OpenInterest(
                symbol=str(raw["symbol"]),
                open_interest=_to_decimal(raw["openInterest"]),
                notional_usd=None,
                ts=_ts_from_ms(raw["time"]),
            )
        except KeyError as exc:
            raise BinanceFuturesError(f"openInterest sin campo {exc}: {raw!r}") from exc

    def get_open_interest_history(
        self, symbol: str, period: str = "1h", limit: int = 30
    ) -> list[OpenInterest]:
        """GET ``/futures/data/openInterestHist`` (serie con nocional en USD)."""
        self._guard()
        raw = self._get(
            OPEN_INTEREST_HIST_PATH,
            params={"symbol": symbol.upper(), "period": period, "limit": int(limit)},
        )
        if not isinstance(raw, list):
            raise BinanceFuturesError(f"respuesta inesperada de openInterestHist: {raw!r}")
        result: list[OpenInterest] = []
        for row in raw:
            if not isinstance(row, Mapping):
                continue
            try:
                result.append(
                    OpenInterest(
                        symbol=str(row["symbol"]),
                        open_interest=_to_decimal(row["sumOpenInterest"]),
                        notional_usd=_to_optional_decimal(row.get("sumOpenInterestValue")),
                        ts=_parse_iso_ts(row["timestamp"]),
                    )
                )
            except KeyError as exc:
                logger.warning("fila de openInterestHist ignorada (falta %s): %r", exc, row)
        return result

    # ---- utilidades internas --------------------------------------------------

    def _guard(self) -> None:
        if self._testnet_only:
            raise DerivativesUnavailable(
                "BINANCE_SPOT_TESTNET_ONLY=true: no hay derivados en Spot Testnet"
            )

    def _get(self, path: str, *, params: Mapping[str, Any]) -> Any:
        url = f"{self.base_url}{path}"
        for attempt in range(self._max_retries + 1):
            delay = self._backoff_base * (2**attempt)
            try:
                response = self._http.get(url, params=params, timeout=self._timeout)
            except httpx.HTTPError as exc:
                if attempt == self._max_retries:
                    raise BinanceFuturesError(f"error de red en {url}: {exc}") from exc
                logger.warning("error de red en %s (%s); reintento en %.1fs", url, exc, delay)
                self._sleep(delay)
                continue

            status = response.status_code
            if status == 200:
                try:
                    return response.json()
                except ValueError as exc:
                    raise BinanceFuturesError(
                        f"respuesta no JSON en {url}: {exc}"
                    ) from exc
            if status not in RETRY_STATUSES:
                raise BinanceFuturesError(
                    f"{status} en {url} (no reintentable)", status_code=status
                )
            if attempt == self._max_retries:
                raise BinanceFuturesError(
                    f"{status} en {url} tras {self._max_retries} reintentos",
                    status_code=status,
                )
            wait = _retry_after(response) or delay
            logger.warning("%s en %s; reintento en %.1fs", status, url, wait)
            self._sleep(wait)
        raise AssertionError("inalcanzable")  # pragma: no cover


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None
