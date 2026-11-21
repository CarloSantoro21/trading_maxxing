"""Fuente de noticias por RSS/Atom gratis (sin llaves), la opción por defecto.

Lee los feeds de ``NEWS_RSS_FEEDS`` (separados por coma; por defecto CoinDesk, Cointelegraph y
Decrypt), los parsea con la librería estándar (``xml.etree``) y devuelve solo los titulares:

- publicados en las últimas ``NEWS_RSS_MAX_AGE_HOURS`` horas (24 por defecto), y
- que mencionan alguno de los activos pedidos (``BTC``, ``ETH`` y las stablecoins), por
  ticker o por nombre (``Bitcoin``, ``Ether``) en el título o en sus categorías.

Si un feed falla se registra y se siguen leyendo los demás; solo si **todos** fallan se lanza
``NewsAPIError`` (el NewsAgent hace fail-open y no veta en ese ciclo).
"""

from __future__ import annotations

import hashlib
import logging
import os
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from tm_agents.news.cryptopanic import NewsAPIError
from tm_agents.news.keywords import detect_assets
from tm_agents.news.models import NewsArticle

logger = logging.getLogger(__name__)

DEFAULT_RSS_FEEDS: tuple[str, ...] = (
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
)
#: Tope de bytes por feed, para no cargar en memoria una respuesta desproporcionada.
MAX_FEED_BYTES = 5_000_000
_ATOM = "{http://www.w3.org/2005/Atom}"
_USER_AGENT = "trading-maxxing-news/1.0 (+rss)"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RSSNewsSource:
    """``NewsSource`` que lee feeds RSS 2.0 o Atom (ver el módulo)."""

    def __init__(
        self,
        feeds: Sequence[str] = DEFAULT_RSS_FEEDS,
        *,
        max_age: timedelta = timedelta(hours=24),
        timeout: float = 10.0,
        http_client: httpx.Client | None = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.feeds = tuple(f.strip() for f in feeds if f and f.strip())
        if not self.feeds:
            raise ValueError("RSSNewsSource necesita al menos un feed (NEWS_RSS_FEEDS)")
        self.max_age = max_age
        self._clock = clock
        self._timeout = timeout
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(
            timeout=timeout, follow_redirects=True, headers={"User-Agent": _USER_AGENT}
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, **kw: Any) -> RSSNewsSource:
        env = os.environ if env is None else env
        raw = env.get("NEWS_RSS_FEEDS") or ""
        feeds = [f for f in (s.strip() for s in raw.split(",")) if f] or list(DEFAULT_RSS_FEEDS)
        hours = float(env.get("NEWS_RSS_MAX_AGE_HOURS") or 24)
        return cls(feeds, max_age=timedelta(hours=hours), **kw)

    def fetch_posts(self, currencies: Iterable[str] = ()) -> list[NewsArticle]:
        """Titulares recientes que mencionan ``currencies``. Lanza ``NewsAPIError``."""
        wanted = {c.upper() for c in currencies}
        cutoff = self._clock() - self.max_age
        articles: dict[str, NewsArticle] = {}
        failures: list[str] = []
        for url in self.feeds:
            try:
                items = self._fetch_feed(url)
            except NewsAPIError as exc:
                logger.warning("feed RSS ignorado en este ciclo: %s", exc)
                failures.append(str(exc))
                continue
            for article in items:
                if article.published_at < cutoff:
                    continue
                if wanted and not wanted.intersection(article.currencies):
                    continue
                articles.setdefault(article.id, article)
        if failures and len(failures) == len(self.feeds):
            raise NewsAPIError("todos los feeds RSS fallaron: " + "; ".join(failures))
        return sorted(articles.values(), key=lambda a: a.published_at, reverse=True)

    def _fetch_feed(self, url: str) -> list[NewsArticle]:
        try:
            resp = self._http.get(url, timeout=self._timeout)
        except httpx.HTTPError as exc:
            raise NewsAPIError(f"{url} no respondió: {exc}") from exc
        if resp.status_code != 200:
            raise NewsAPIError(f"{url} respondió HTTP {resp.status_code}")
        if len(resp.content) > MAX_FEED_BYTES:
            raise NewsAPIError(f"{url} excede {MAX_FEED_BYTES} bytes")
        try:
            return parse_feed(resp.content, source=_host(url))
        except ET.ParseError as exc:
            raise NewsAPIError(f"{url} no es XML válido: {exc}") from exc

    def close(self) -> None:
        if self._owns_client:
            self._http.close()


def parse_feed(content: bytes | str, *, source: str | None = None) -> list[NewsArticle]:
    """Parsea un feed RSS 2.0 o Atom. Las entradas sin título o sin fecha se ignoran."""
    root = ET.fromstring(content)
    entries = root.findall("./channel/item") or root.findall(f"{_ATOM}entry")
    out: list[NewsArticle] = []
    for entry in entries:
        article = _parse_entry(entry, source)
        if article is not None:
            out.append(article)
    return out


def _parse_entry(entry: ET.Element, source: str | None) -> NewsArticle | None:
    title = _text(entry, "title", f"{_ATOM}title")
    link = _text(entry, "link") or _atom_link(entry)
    guid = _text(entry, "guid", f"{_ATOM}id") or link or title
    published = _parse_date(
        _text(entry, "pubDate", f"{_ATOM}published", f"{_ATOM}updated", "updated")
    )
    if not title or published is None:
        return None
    categories = [c.text or c.get("term") or "" for c in entry.findall("category")]
    categories += [c.get("term") or "" for c in entry.findall(f"{_ATOM}category")]
    currencies = tuple(detect_assets(" ".join([title, *categories])))
    digest = hashlib.sha1(f"{source}|{guid}".encode()).hexdigest()[:16]
    return NewsArticle(
        id=f"rss:{digest}",
        title=title,
        published_at=published,
        currencies=currencies,
        source=source,
        url=link,
    )


def _text(entry: ET.Element, *tags: str) -> str | None:
    for tag in tags:
        node = entry.find(tag)
        if node is not None and node.text and node.text.strip():
            return node.text.strip()
    return None


def _atom_link(entry: ET.Element) -> str | None:
    for node in entry.findall(f"{_ATOM}link"):
        if node.get("rel", "alternate") == "alternate" and node.get("href"):
            return node.get("href")
    return None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)  # RFC 822 (RSS)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))  # ISO 8601 (Atom)
        except ValueError:
            return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _host(url: str) -> str | None:
    host = urlparse(url).hostname
    return host.removeprefix("www.") if host else None


__all__ = ["DEFAULT_RSS_FEEDS", "RSSNewsSource", "parse_feed"]
