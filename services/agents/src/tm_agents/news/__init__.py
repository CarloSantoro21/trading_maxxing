"""NewsAgent: noticias de CryptoPanic, clasificación con LLM y vetos con expiración."""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import timedelta

from tm_agents.llm import build_llm_client
from tm_agents.news.agent import NewsAgent, RefreshResult, VetoPolicy, split_symbol
from tm_agents.news.cryptopanic import CryptoPanicClient, NewsAPIError
from tm_agents.news.models import (
    GLOBAL_SCOPE,
    HeadlineClassification,
    Impact,
    NewsArticle,
    NewsClassificationBatch,
    Veto,
)
from tm_agents.news.veto import InMemoryVetoStore, RedisVetoStore, VetoStore, veto_store_from_env


def news_agent_from_env(env: Mapping[str, str] | None = None) -> NewsAgent:
    """Arma el NewsAgent con `LLM_PROVIDER`, `CRYPTOPANIC_TOKEN` y `REDIS_URL`.

    Duraciones configurables con `NEWS_VETO_HIGH_HOURS` (4) y `NEWS_VETO_CRITICAL_HOURS` (24).
    """
    env = os.environ if env is None else env
    policy = VetoPolicy(
        high_duration=timedelta(hours=float(env.get("NEWS_VETO_HIGH_HOURS") or 4)),
        critical_duration=timedelta(hours=float(env.get("NEWS_VETO_CRITICAL_HOURS") or 24)),
    )
    return NewsAgent(
        build_llm_client(env),
        CryptoPanicClient.from_env(env),
        veto_store_from_env(env),
        policy=policy,
    )


__all__ = [
    "GLOBAL_SCOPE",
    "CryptoPanicClient",
    "HeadlineClassification",
    "Impact",
    "InMemoryVetoStore",
    "NewsAPIError",
    "NewsAgent",
    "NewsArticle",
    "NewsClassificationBatch",
    "RedisVetoStore",
    "RefreshResult",
    "Veto",
    "VetoPolicy",
    "VetoStore",
    "news_agent_from_env",
    "split_symbol",
]
