"""NewsAgent: clasifica titulares con un LLM y veta nuevas entradas.

Solo puede **vetar nuevas entradas**; nunca abre ni cierra operaciones y las posiciones
abiertas conservan su SL y TP. Política por defecto (issue #17, configurable):

| Impacto | Alcance | Duración |
|---|---|---|
| low | ninguno | — |
| high | activo afectado (`BTC` bloquea `BTCUSDT`) | 4 h |
| critical | todo el mercado | 24 h |

La duración se cuenta desde la publicación del titular, así reprocesar una noticia vieja no
alarga el veto, y una noticia que ya caducó no veta.

**Fail-open:** si la API de noticias o el LLM fallan (o responden algo inválido), el agente
no veta, registra un warning y lo reintenta en el siguiente ciclo. Preferimos operar sin el
filtro de noticias a detener el sistema por una dependencia externa; el RiskAgent y los SL
siguen protegiendo el capital.
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from tm_agents.base import AgentContext
from tm_agents.llm.base import LLMClient, LLMError
from tm_agents.news.cryptopanic import NewsAPIError
from tm_agents.news.models import (
    GLOBAL_SCOPE,
    HeadlineClassification,
    Impact,
    NewsArticle,
    NewsClassificationBatch,
    Veto,
)
from tm_agents.news.veto import InMemoryVetoStore, VetoStore

logger = logging.getLogger(__name__)

QUOTE_ASSETS = ("USDT", "USDC", "FDUSD", "BUSD")

SYSTEM_PROMPT = (
    "You are a risk analyst for a crypto spot trading system. Classify each news headline "
    "by its market impact. Use 'critical' only for market-wide events (stablecoin depeg, "
    "collapse or insolvency of a major exchange, systemic hack). Use 'high' for events that "
    "hit specific assets (exchange hack, SEC or regulator action against an asset, chain "
    "halt, major exploit). Everything else is 'low'. List the affected assets as tickers "
    "(BTC, ETH, USDT). Sentiment goes from -1 (very negative) to 1 (very positive). "
    "Answer only with JSON matching the schema, one item per headline, reusing each id."
)


class NewsSource(Protocol):
    def fetch_posts(self, currencies: Iterable[str] = ()) -> list[NewsArticle]: ...


@dataclass(frozen=True)
class VetoPolicy:
    high_duration: timedelta = timedelta(hours=4)
    critical_duration: timedelta = timedelta(hours=24)

    def duration(self, impact: Impact) -> timedelta | None:
        return {
            Impact.HIGH: self.high_duration,
            Impact.CRITICAL: self.critical_duration,
        }.get(impact)


@dataclass
class RefreshResult:
    """Resumen de un ciclo del NewsAgent (para logs y métricas)."""

    articles: int = 0
    classified: list[tuple[NewsArticle, HeadlineClassification]] = field(default_factory=list)
    vetoes: list[Veto] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def failed_open(self) -> bool:
        return bool(self.errors)


def split_symbol(symbol: str, quotes: Sequence[str] = QUOTE_ASSETS) -> tuple[str, str | None]:
    """`BTCUSDT` -> (`BTC`, `USDT`). Si no reconoce la cotización devuelve (símbolo, None)."""
    s = symbol.upper()
    for q in quotes:
        if s.endswith(q) and len(s) > len(q):
            return s[: -len(q)], q
    return s, None


def _utcnow() -> datetime:
    return datetime.now(UTC)


class NewsAgent:
    def __init__(
        self,
        llm: LLMClient,
        source: NewsSource,
        store: VetoStore | None = None,
        *,
        policy: VetoPolicy | None = None,
        clock: Callable[[], datetime] = _utcnow,
        batch_size: int = 20,
        max_seen: int = 2000,
        extra_assets: Sequence[str] = ("USDT", "USDC"),
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size debe ser al menos 1")
        self.llm = llm
        self.source = source
        self.store = store if store is not None else InMemoryVetoStore(clock)
        self.policy = policy or VetoPolicy()
        self._clock = clock
        self.batch_size = batch_size
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._max_seen = max_seen
        # Stablecoins: sus noticias (pérdida de paridad) afectan a todo el mercado
        self.extra_assets = tuple(a.upper() for a in extra_assets)

    # --- 1. noticias ---------------------------------------------------------------------
    def fetch_news(self, symbols: Iterable[str]) -> list[NewsArticle]:
        """Titulares nuevos de los activos de `symbols`. Lanza `NewsAPIError`."""
        assets = sorted({split_symbol(s)[0] for s in symbols} | set(self.extra_assets))
        articles = self.source.fetch_posts(assets)
        return [a for a in articles if a.id not in self._seen]

    # --- 2. clasificación ----------------------------------------------------------------
    def classify(
        self, articles: Sequence[NewsArticle]
    ) -> list[tuple[NewsArticle, HeadlineClassification]]:
        """Clasifica en lotes. Lanza `LLMError` si el LLM no da una respuesta válida."""
        out: list[tuple[NewsArticle, HeadlineClassification]] = []
        for i in range(0, len(articles), self.batch_size):
            batch = articles[i : i + self.batch_size]
            by_id = {a.id: a for a in batch}
            result = self.llm.complete_json(
                _build_prompt(batch), NewsClassificationBatch, system=SYSTEM_PROMPT
            )
            for item in result.items:
                article = by_id.get(item.id)
                if article is None:
                    logger.warning("el LLM clasificó un id desconocido: %s", item.id)
                    continue
                out.append((article, item))
            for article_id in by_id:
                self._mark_seen(article_id)
        return out

    # --- 3. política de veto -------------------------------------------------------------
    def vetoes_for(self, article: NewsArticle, c: HeadlineClassification) -> list[Veto]:
        duration = self.policy.duration(c.impact)
        if duration is None:
            return []
        now = self._clock()
        expires_at = min(article.published_at, now) + duration
        if expires_at <= now:
            return []
        reason = f"{c.impact.value}: {article.title}"[:300]
        if c.impact is Impact.CRITICAL:
            scopes = [GLOBAL_SCOPE]
        else:
            scopes = c.symbols or list(article.currencies)
            if not scopes:
                logger.warning("noticia de impacto alto sin activos, no se veta: %s", article.id)
        return [
            Veto(
                scope=s,
                impact=c.impact,
                reason=reason,
                article_id=article.id,
                expires_at=expires_at,
            )
            for s in scopes
        ]

    def apply(self, classified: Iterable[tuple[NewsArticle, HeadlineClassification]]) -> list[Veto]:
        applied: list[Veto] = []
        for article, c in classified:
            for veto in self.vetoes_for(article, c):
                if self.store.put(veto):
                    logger.warning(
                        "veto %s sobre %s hasta %s: %s",
                        veto.impact.value,
                        "todo el mercado" if veto.is_global else veto.scope,
                        veto.expires_at.isoformat(),
                        veto.reason,
                    )
                    applied.append(veto)
        return applied

    # --- ciclo completo ------------------------------------------------------------------
    def refresh(self, symbols: Iterable[str]) -> RefreshResult:
        """Noticias -> clasificación -> vetos. Nunca lanza por fallas externas (fail-open)."""
        result = RefreshResult()
        try:
            articles = self.fetch_news(symbols)
        except NewsAPIError as exc:
            logger.warning("fail-open: sin noticias en este ciclo, no se veta (%s)", exc)
            result.errors.append(f"news: {exc}")
            return result
        result.articles = len(articles)
        if not articles:
            return result
        try:
            result.classified = self.classify(articles)
        except LLMError as exc:
            logger.warning("fail-open: el LLM falló, no se veta en este ciclo (%s)", exc)
            result.errors.append(f"llm: {exc}")
            return result
        result.vetoes = self.apply(result.classified)
        return result

    # --- consulta ------------------------------------------------------------------------
    def active_veto(self, symbol: str) -> Veto | None:
        """Veto vigente que bloquea nuevas entradas en `symbol` (global primero)."""
        base, quote = split_symbol(symbol)
        for scope in (GLOBAL_SCOPE, base, quote):
            if scope and (veto := self.store.get(scope)) is not None:
                return veto
        return None

    def veto(self, ctx: AgentContext) -> Veto | None:
        """Interfaz del orquestador: `None` si se puede entrar en `ctx.symbol`."""
        return self.active_veto(ctx.symbol)

    def _mark_seen(self, article_id: str) -> None:
        self._seen[article_id] = None
        while len(self._seen) > self._max_seen:
            self._seen.popitem(last=False)


def _build_prompt(articles: Sequence[NewsArticle]) -> str:
    lines = [
        json.dumps(
            {
                "id": a.id,
                "title": a.title,
                "assets": list(a.currencies),
                "source": a.source,
                "published_at": a.published_at.isoformat(),
            },
            ensure_ascii=False,
        )
        for a in articles
    ]
    return "Classify these headlines (one JSON object per line):\n" + "\n".join(lines)
