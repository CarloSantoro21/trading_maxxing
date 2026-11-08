"""Modelos del NewsAgent: titulares, clasificación del LLM y vetos."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

GLOBAL_SCOPE = "*"


class Impact(StrEnum):
    LOW = "low"
    HIGH = "high"
    CRITICAL = "critical"


class NewsArticle(BaseModel):
    """Titular normalizado (independiente de la API de noticias)."""

    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    published_at: datetime
    currencies: tuple[str, ...] = ()
    source: str | None = None
    url: str | None = None

    @field_validator("published_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)


class HeadlineClassification(BaseModel):
    """Lo que el LLM dice de un titular. Es la salida JSON validada con Pydantic."""

    id: str = Field(description="id del titular tal como viene en el prompt")
    impact: Impact = Field(description="low, high o critical")
    symbols: list[str] = Field(
        default_factory=list, description="Activos afectados, en ticker (BTC, ETH, USDT)"
    )
    sentiment: float = Field(ge=-1.0, le=1.0, description="-1 muy negativo, 1 muy positivo")
    reason: str = Field(default="", max_length=500)

    @field_validator("symbols")
    @classmethod
    def _upper(cls, v: list[str]) -> list[str]:
        return sorted({s.strip().upper() for s in v if s and s.strip()})


class NewsClassificationBatch(BaseModel):
    """Respuesta del LLM para un lote de titulares."""

    items: list[HeadlineClassification]


class Veto(BaseModel):
    """Bloqueo de nuevas entradas: por activo (`BTC`) o global (`*`) hasta `expires_at`."""

    model_config = ConfigDict(frozen=True)

    scope: str
    impact: Impact
    reason: str
    article_id: str
    expires_at: datetime

    @property
    def is_global(self) -> bool:
        return self.scope == GLOBAL_SCOPE

    def ttl_seconds(self, now: datetime) -> int:
        return max(int((self.expires_at - now).total_seconds()), 0)
