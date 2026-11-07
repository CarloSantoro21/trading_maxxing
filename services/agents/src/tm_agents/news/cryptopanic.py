"""Cliente de CryptoPanic (API v2, `GET /api/<plan>/v2/posts/`).

La API v2 pone el plan en la ruta (`developer`, `growth`, `enterprise`) y autentica con el
parámetro `auth_token`. Los activos de cada post vienen en `instruments` (v2) o en
`currencies` (v1); se aceptan ambos. Configuración por entorno:
`CRYPTOPANIC_TOKEN`, `CRYPTOPANIC_PLAN` (por defecto `developer`) y `CRYPTOPANIC_BASE_URL`.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Mapping
from typing import Any

import httpx
from pydantic import ValidationError

from tm_agents.news.models import NewsArticle

logger = logging.getLogger(__name__)

CRYPTOPANIC_ROOT = "https://cryptopanic.com/api"


class NewsAPIError(RuntimeError):
    """La API de noticias no respondió o respondió algo inesperado."""


class CryptoPanicClient:
    def __init__(
        self,
        token: str,
        *,
        plan: str = "developer",
        base_url: str | None = None,
        timeout: float = 10.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not token:
            raise ValueError("CryptoPanicClient necesita CRYPTOPANIC_TOKEN")
        self._token = token
        self.base_url = (base_url or f"{CRYPTOPANIC_ROOT}/{plan}/v2").rstrip("/")
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(timeout=timeout)
        self._timeout = timeout

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, **kw: Any) -> CryptoPanicClient:
        env = os.environ if env is None else env
        return cls(
            env.get("CRYPTOPANIC_TOKEN", ""),
            plan=env.get("CRYPTOPANIC_PLAN") or "developer",
            base_url=env.get("CRYPTOPANIC_BASE_URL") or None,
            **kw,
        )

    def fetch_posts(
        self,
        currencies: Iterable[str] = (),
        *,
        kind: str = "news",
        filter: str | None = None,
        public: bool = True,
    ) -> list[NewsArticle]:
        """Titulares recientes de `currencies` (tickers como BTC, ETH). Lanza `NewsAPIError`."""
        params: dict[str, str] = {"auth_token": self._token, "kind": kind}
        codes = sorted({c.upper() for c in currencies})
        if codes:
            params["currencies"] = ",".join(codes)
        if filter:
            params["filter"] = filter
        if public:
            params["public"] = "true"
        try:
            resp = self._http.get(f"{self.base_url}/posts/", params=params, timeout=self._timeout)
        except httpx.HTTPError as exc:
            raise NewsAPIError(f"CryptoPanic no respondió: {exc}") from exc
        if resp.status_code != 200:
            raise NewsAPIError(f"CryptoPanic respondió HTTP {resp.status_code}")
        try:
            results = resp.json()["results"]
        except (ValueError, KeyError, TypeError) as exc:
            raise NewsAPIError(f"respuesta de CryptoPanic inesperada: {exc}") from exc
        articles = []
        for raw in results or []:
            article = _parse_post(raw)
            if article is not None:
                articles.append(article)
        return articles

    def close(self) -> None:
        if self._owns_client:
            self._http.close()


def _parse_post(raw: Mapping[str, Any]) -> NewsArticle | None:
    instruments = raw.get("instruments") or raw.get("currencies") or []
    source = raw.get("source") or {}
    try:
        return NewsArticle(
            id=str(raw["id"]),
            title=str(raw["title"]),
            published_at=raw.get("published_at") or raw["created_at"],
            currencies=tuple(str(i["code"]).upper() for i in instruments if i.get("code")),
            source=source.get("title") if isinstance(source, Mapping) else None,
            url=raw.get("original_url") or raw.get("url"),
        )
    except (KeyError, TypeError, AttributeError, ValidationError) as exc:
        logger.warning("post de CryptoPanic ignorado (%s): %r", exc, raw.get("id"))
        return None
