"""Clasificador de titulares por palabras clave, **sin LLM** y sin costo.

Se usa cuando ``NEWS_ENABLED=true`` y ``LLM_PROVIDER=none``. Es deliberadamente conservador
y fácil de auditar: busca términos que en la práctica acompañan a los eventos que importan
para el veto (hackeos, exploits, acciones de reguladores, deslistados, pérdida de paridad de
una stablecoin, quiebras de exchanges).

| Impacto | Cuándo |
|---|---|
| ``critical`` | depeg, insolvencia/quiebra, retiros congelados o caída de un exchange grande |
| ``high`` | hack, exploit, robo, SEC/CFTC/DOJ, demanda, prohibición, delist, sanción, etc. |
| ``low`` | todo lo demás |

Los activos afectados salen de ``NewsArticle.currencies`` y de los tickers o nombres que
aparezcan en el titular (``Bitcoin`` → ``BTC``, ``Ether`` → ``ETH``, ``Tether`` → ``USDT``).
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from tm_agents.news.models import HeadlineClassification, Impact, NewsArticle

#: Términos de impacto crítico (todo el mercado). Sin distinguir mayúsculas.
CRITICAL_KEYWORDS: tuple[str, ...] = (
    r"de-?peg(ged|s)?",
    r"los(es|t) (its )?peg",
    r"insolven(t|cy)",
    r"bankrupt(cy)?",
    r"chapter 11",
    r"(halts?|suspend(s|ed)?|paus(es|ed)|freez(es|e)|froze) withdrawals",
    r"withdrawals? (halted|suspended|paused|frozen)",
    r"systemic",
    r"contagion",
)

#: Términos de impacto alto (activo afectado). Sin distinguir mayúsculas.
HIGH_KEYWORDS: tuple[str, ...] = (
    r"hack(s|ed|ers?)?",
    r"exploit(s|ed)?",
    r"breach(es|ed)?",
    r"stolen",
    r"theft",
    r"drain(s|ed)?",
    r"rug ?pull(s|ed)?",
    r"attack(s|ed)?",
    r"vulnerabilit(y|ies)",
    r"lawsuit",
    r"sues",
    r"sued",
    r"indict(s|ed|ment)?",
    r"charg(es|ed)",
    r"crackdown",
    r"ban(s|ned)?",
    r"delist(s|ed|ing)?",
    r"sanction(s|ed)?",
    r"seiz(e|es|ed|ure)",
    r"subpoena(s|ed)?",
    r"investigat(ion|es|ed|ing)",
    r"outage",
    r"halt(s|ed)?",
    r"chain (halt|split|reorg)",
    r"emergency",
    r"liquidation cascade",
    r"flash crash",
)

#: Siglas de reguladores: se exigen en mayúsculas para no confundir ``SEC`` con "second".
REGULATOR_ACRONYMS: tuple[str, ...] = ("SEC", "CFTC", "DOJ", "FBI", "OFAC", "FinCEN")

#: Nombre o ticker en el titular → ticker que se veta.
ASSET_ALIASES: dict[str, str] = {
    "BTC": "BTC",
    "BITCOIN": "BTC",
    "XBT": "BTC",
    "ETH": "ETH",
    "ETHER": "ETH",
    "ETHEREUM": "ETH",
    "USDT": "USDT",
    "TETHER": "USDT",
    "USDC": "USDC",
}

_CRITICAL_RE = re.compile(r"\b(" + "|".join(CRITICAL_KEYWORDS) + r")\b", re.IGNORECASE)
_HIGH_RE = re.compile(r"\b(" + "|".join(HIGH_KEYWORDS) + r")\b", re.IGNORECASE)
_REGULATOR_RE = re.compile(r"\b(" + "|".join(REGULATOR_ACRONYMS) + r")\b")
_ASSET_RE = re.compile(r"\b(" + "|".join(ASSET_ALIASES) + r")\b", re.IGNORECASE)


def detect_assets(text: str) -> list[str]:
    """Tickers mencionados en ``text`` (``"Bitcoin ETF"`` → ``["BTC"]``)."""
    return sorted({ASSET_ALIASES[m.upper()] for m in _ASSET_RE.findall(text)})


def classify_headline(article: NewsArticle) -> HeadlineClassification:
    """Clasifica un titular por palabras clave (ver el módulo)."""
    title = article.title
    symbols = sorted(set(article.currencies) | set(detect_assets(title)))
    if match := _CRITICAL_RE.search(title):
        impact, sentiment, reason = Impact.CRITICAL, -0.9, match.group(0)
    elif match := (_REGULATOR_RE.search(title) or _HIGH_RE.search(title)):
        impact, sentiment, reason = Impact.HIGH, -0.6, match.group(0)
    else:
        impact, sentiment, reason = Impact.LOW, 0.0, ""
    return HeadlineClassification(
        id=article.id,
        impact=impact,
        symbols=symbols,
        sentiment=sentiment,
        reason=f"palabra clave: {reason}" if reason else "",
    )


class KeywordClassifier:
    """``HeadlineClassifier`` sin LLM: nunca hace red y nunca falla."""

    name = "keywords"

    def classify(self, articles: Sequence[NewsArticle]) -> list[HeadlineClassification]:
        return [classify_headline(a) for a in articles]


__all__ = [
    "ASSET_ALIASES",
    "CRITICAL_KEYWORDS",
    "HIGH_KEYWORDS",
    "REGULATOR_ACRONYMS",
    "KeywordClassifier",
    "classify_headline",
    "detect_assets",
]
