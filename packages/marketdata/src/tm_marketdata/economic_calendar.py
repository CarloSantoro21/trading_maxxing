"""Calendario económico (YAML manual + Finnhub con fallback).

El StrategyAuditorAgent (#108) usa este calendario en la Regla 4 (bloquear trading cerca
de eventos macro de alto impacto). La fuente por defecto es un YAML manual en
``docs/calendario-economico.yml``; ``FinnhubEconomicCalendar`` intenta Finnhub con
``FINNHUB_API_KEY`` y cae al YAML si la llave falta o la API falla.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx
import yaml
from pydantic import BaseModel

logger = logging.getLogger(__name__)

DEFAULT_CALENDAR_PATH = Path("docs/calendario-economico.yml")
FINNHUB_URL = "https://finnhub.io/api/v1/calendar/economic"

ImpactLevel = Literal["low", "medium", "high"]
_IMPACT_MAP: Mapping[str, ImpactLevel] = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "1": "low",
    "2": "medium",
    "3": "high",
}


class MacroEvent(BaseModel):
    event: str
    scheduled_at: datetime
    impact: ImpactLevel
    region: str = "US"
    actual: str | None = None
    forecast: str | None = None
    previous: str | None = None
    source: str = "manual"


class EconomicCalendar(Protocol):
    def events_within(self, hours: int, now: datetime | None = None) -> list[MacroEvent]: ...


def _now_utc(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    return now if now.tzinfo else now.replace(tzinfo=UTC)


def _filter_horizon(
    events: Iterable[MacroEvent], hours: int, now: datetime | None
) -> list[MacroEvent]:
    start = _now_utc(now)
    end = start + timedelta(hours=hours)
    chosen = [ev for ev in events if start <= _coerce_tz(ev.scheduled_at) <= end]
    chosen.sort(key=lambda e: _coerce_tz(e.scheduled_at))
    return chosen


def _coerce_tz(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class YamlEconomicCalendar:
    """Lee eventos macro de un YAML manual. Falta de archivo → lista vacía."""

    def __init__(self, path: Path | str = DEFAULT_CALENDAR_PATH) -> None:
        self.path = Path(path)

    def events_within(self, hours: int, now: datetime | None = None) -> list[MacroEvent]:
        events = self._load_all()
        return _filter_horizon(events, hours, now)

    def _load_all(self) -> list[MacroEvent]:
        if not self.path.exists():
            logger.info("calendario YAML no encontrado en %s; sin eventos", self.path)
            return []
        try:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ValueError(f"calendario YAML inválido en {self.path}: {exc}") from exc
        if raw is None:
            return []
        if not isinstance(raw, Mapping):
            raise ValueError(f"calendario YAML inválido en {self.path}: raíz debe ser un mapping")
        rows = raw.get("events") or []
        if not isinstance(rows, list):
            raise ValueError(
                f"calendario YAML inválido en {self.path}: 'events' debe ser una lista"
            )
        events: list[MacroEvent] = []
        for row in rows:
            if not isinstance(row, Mapping):
                logger.warning("entrada de calendario ignorada (no es mapping): %r", row)
                continue
            events.append(
                MacroEvent(
                    event=str(row["event"]),
                    scheduled_at=row["scheduled_at"],
                    impact=_IMPACT_MAP.get(str(row.get("impact", "low")).lower(), "low"),
                    region=str(row.get("region", "US")),
                    actual=_optional_str(row.get("actual")),
                    forecast=_optional_str(row.get("forecast")),
                    previous=_optional_str(row.get("previous")),
                    source=str(row.get("source", "manual")),
                )
            )
        return events


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


class FinnhubEconomicCalendar:
    """Calendario de Finnhub. Sin ``FINNHUB_API_KEY`` o ante error cae al YAML."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        fallback: EconomicCalendar | None = None,
        base_url: str = FINNHUB_URL,
        timeout: float = 10.0,
        http_client: httpx.Client | None = None,
        env: Mapping[str, str] | None = None,
        yaml_path: Path | str = DEFAULT_CALENDAR_PATH,
    ) -> None:
        env = os.environ if env is None else env
        self._api_key = api_key if api_key is not None else env.get("FINNHUB_API_KEY") or ""
        self._fallback: EconomicCalendar = fallback or YamlEconomicCalendar(yaml_path)
        self._base_url = base_url
        self._timeout = timeout
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def events_within(self, hours: int, now: datetime | None = None) -> list[MacroEvent]:
        if not self._api_key:
            logger.info("FINNHUB_API_KEY ausente; usando calendario YAML")
            return self._fallback.events_within(hours, now)
        start = _now_utc(now)
        end = start + timedelta(hours=hours)
        params = {
            "from": start.date().isoformat(),
            "to": end.date().isoformat(),
            "token": self._api_key,
        }
        try:
            resp = self._http.get(self._base_url, params=params, timeout=self._timeout)
            resp.raise_for_status()
            payload = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Finnhub falló (%s); usando calendario YAML", exc)
            return self._fallback.events_within(hours, now)
        rows = _extract_finnhub_rows(payload)
        events = [_parse_finnhub_row(row) for row in rows]
        events = [ev for ev in events if ev is not None]
        return _filter_horizon(events, hours, now)


def _extract_finnhub_rows(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, Mapping):
        maybe = payload.get("economicCalendar") or payload.get("data") or []
        if isinstance(maybe, list):
            return [r for r in maybe if isinstance(r, Mapping)]
    return []


_FINNHUB_IMPACT: Mapping[str, ImpactLevel] = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "1": "low",
    "2": "medium",
    "3": "high",
}


def _parse_finnhub_row(row: Mapping[str, Any]) -> MacroEvent | None:
    try:
        when = row.get("time") or row.get("date")
        if when is None:
            return None
        scheduled = _parse_finnhub_ts(when)
        return MacroEvent(
            event=str(row.get("event") or row.get("eventName") or "Evento macro"),
            scheduled_at=scheduled,
            impact=_FINNHUB_IMPACT.get(str(row.get("impact", "low")).lower(), "low"),
            region=str(row.get("country") or row.get("region") or "US"),
            actual=_optional_str(row.get("actual")),
            forecast=_optional_str(row.get("estimate") or row.get("forecast")),
            previous=_optional_str(row.get("prev") or row.get("previous")),
            source="finnhub",
        )
    except (ValueError, TypeError) as exc:
        logger.warning("fila de Finnhub ignorada (%s): %r", exc, row)
        return None


def _parse_finnhub_ts(value: Any) -> datetime:
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value), tz=UTC)
    text = str(value).strip()
    # "2026-12-10 19:00:00" o "2026-12-10T19:00:00Z"
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError as exc:
        raise ValueError(f"timestamp Finnhub inválido: {value!r}") from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
