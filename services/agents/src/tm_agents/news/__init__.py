"""NewsAgent: titulares (RSS gratis o CryptoPanic), clasificación (LLM o palabras clave) y
vetos con expiración.

Todo es **opcional y está apagado por defecto** (``NEWS_ENABLED=false``): sin noticias no hay
llamadas de red, ni llaves, ni filtro (el orquestador recibe ``news=None``). Al prenderlo:

- ``NEWS_SOURCE=rss`` (por defecto, gratis) o ``cryptopanic`` (``CRYPTOPANIC_TOKEN``);
- ``LLM_PROVIDER=none`` (por defecto) clasifica por palabras clave, sin costo; cualquier otro
  proveedor (``workers-ai``, ``groq``, ``openai-compatible``) clasifica con el LLM.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from tm_agents.llm import LLMClient, build_llm_client
from tm_agents.news.agent import (
    HeadlineClassifier,
    LLMHeadlineClassifier,
    NewsAgent,
    NewsSource,
    RefreshResult,
    VetoPolicy,
    split_symbol,
)
from tm_agents.news.cryptopanic import CryptoPanicClient, NewsAPIError
from tm_agents.news.keywords import KeywordClassifier
from tm_agents.news.models import (
    GLOBAL_SCOPE,
    HeadlineClassification,
    Impact,
    NewsArticle,
    NewsClassificationBatch,
    Veto,
)
from tm_agents.news.rss import DEFAULT_RSS_FEEDS, RSSNewsSource
from tm_agents.news.veto import InMemoryVetoStore, RedisVetoStore, VetoStore, veto_store_from_env

logger = logging.getLogger(__name__)

SUPPORTED_SOURCES = ("rss", "cryptopanic")
_UNSET: Any = object()


def news_source_from_env(env: Mapping[str, str] | None = None) -> NewsSource:
    """Fuente de ``NEWS_SOURCE``: ``rss`` (por defecto) o ``cryptopanic``."""
    env = os.environ if env is None else env
    source = (env.get("NEWS_SOURCE") or "rss").strip().lower()
    if source == "rss":
        return RSSNewsSource.from_env(env)
    if source == "cryptopanic":
        return CryptoPanicClient.from_env(env)
    raise ValueError(
        f"NEWS_SOURCE desconocido: '{source}' (soportados: {', '.join(SUPPORTED_SOURCES)})"
    )


def news_agent_from_env(
    env: Mapping[str, str] | None = None, *, llm: LLMClient | None = _UNSET
) -> NewsAgent:
    """Arma el NewsAgent con ``NEWS_SOURCE``, ``LLM_PROVIDER`` y ``REDIS_URL``.

    ``llm`` permite reutilizar un cliente ya construido (el worker comparte uno con el
    ExplainerAgent); si no se pasa, se construye con ``build_llm_client``. Con ``None`` se
    clasifica por palabras clave. Duraciones de veto configurables con
    ``NEWS_VETO_HIGH_HOURS`` (4) y ``NEWS_VETO_CRITICAL_HOURS`` (24).
    """
    env = os.environ if env is None else env
    policy = VetoPolicy(
        high_duration=timedelta(hours=float(env.get("NEWS_VETO_HIGH_HOURS") or 4)),
        critical_duration=timedelta(hours=float(env.get("NEWS_VETO_CRITICAL_HOURS") or 24)),
    )
    client = build_llm_client(env) if llm is _UNSET else llm
    if client is None:
        logger.info("NewsAgent sin LLM: clasificación por palabras clave")
    return NewsAgent(
        client,
        news_source_from_env(env),
        veto_store_from_env(env),
        policy=policy,
    )


__all__ = [
    "DEFAULT_RSS_FEEDS",
    "GLOBAL_SCOPE",
    "SUPPORTED_SOURCES",
    "CryptoPanicClient",
    "HeadlineClassification",
    "HeadlineClassifier",
    "Impact",
    "InMemoryVetoStore",
    "KeywordClassifier",
    "LLMHeadlineClassifier",
    "NewsAPIError",
    "NewsAgent",
    "NewsArticle",
    "NewsClassificationBatch",
    "NewsSource",
    "RSSNewsSource",
    "RedisVetoStore",
    "RefreshResult",
    "Veto",
    "VetoPolicy",
    "VetoStore",
    "news_agent_from_env",
    "news_source_from_env",
    "split_symbol",
]
