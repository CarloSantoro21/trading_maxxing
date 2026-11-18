"""Tests del calendario económico (YAML manual + Finnhub con fallback)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from tm_marketdata.economic_calendar import (
    FinnhubEconomicCalendar,
    MacroEvent,
    YamlEconomicCalendar,
)

NOW = datetime(2026, 12, 10, 12, 0, tzinfo=UTC)


def _write_yaml(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_yaml_load_and_filter_by_horizon(tmp_path):
    path = _write_yaml(
        tmp_path / "cal.yml",
        """
events:
  - event: "FOMC rate decision"
    scheduled_at: "2026-12-10T19:00:00Z"
    impact: high
    region: US
  - event: "US CPI"
    scheduled_at: "2026-12-11T13:30:00Z"
    impact: high
  - event: "Evento viejo"
    scheduled_at: "2026-12-01T00:00:00Z"
    impact: medium
  - event: "Evento futuro lejano"
    scheduled_at: "2027-01-15T00:00:00Z"
    impact: low
""",
    )
    cal = YamlEconomicCalendar(path)
    rows = cal.events_within(hours=24, now=NOW)
    assert [r.event for r in rows] == ["FOMC rate decision"]

    wider = cal.events_within(hours=48, now=NOW)
    assert [r.event for r in wider] == ["FOMC rate decision", "US CPI"]
    assert all(isinstance(r, MacroEvent) for r in wider)
    assert wider[0].impact == "high"
    assert wider[0].region == "US"


def test_yaml_missing_file_returns_empty(tmp_path):
    cal = YamlEconomicCalendar(tmp_path / "no-existe.yml")
    assert cal.events_within(hours=24, now=NOW) == []


def test_yaml_malformed_raises_with_path(tmp_path):
    path = _write_yaml(tmp_path / "bad.yml", "events: [\n  - broken: {")
    cal = YamlEconomicCalendar(path)
    with pytest.raises(ValueError, match=str(path)):
        cal.events_within(hours=24, now=NOW)


def test_yaml_root_not_mapping_raises(tmp_path):
    path = _write_yaml(tmp_path / "root.yml", "- just-a-list\n- otro\n")
    cal = YamlEconomicCalendar(path)
    with pytest.raises(ValueError, match="mapping"):
        cal.events_within(hours=24, now=NOW)


def test_finnhub_falls_back_to_yaml_when_key_missing(tmp_path):
    path = _write_yaml(
        tmp_path / "cal.yml",
        """
events:
  - event: "FOMC rate decision"
    scheduled_at: "2026-12-10T19:00:00Z"
    impact: high
""",
    )

    def handler(request):  # pragma: no cover - no debería llamarse
        raise AssertionError("no debe pegarle a Finnhub sin API key")

    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="https://finnhub.io")
    cal = FinnhubEconomicCalendar(
        api_key="",
        yaml_path=path,
        http_client=http,
        env={},
    )
    rows = cal.events_within(hours=24, now=NOW)
    assert [r.event for r in rows] == ["FOMC rate decision"]
    assert rows[0].source == "manual"


def test_finnhub_falls_back_on_api_error(tmp_path):
    path = _write_yaml(
        tmp_path / "cal.yml",
        """
events:
  - event: "FOMC rate decision"
    scheduled_at: "2026-12-10T19:00:00Z"
    impact: high
""",
    )

    def handler(request):
        return httpx.Response(500, json={"error": "boom"})

    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="https://finnhub.io")
    cal = FinnhubEconomicCalendar(
        api_key="k",
        yaml_path=path,
        http_client=http,
        env={},
    )
    rows = cal.events_within(hours=24, now=NOW)
    assert [r.event for r in rows] == ["FOMC rate decision"]


def test_finnhub_happy_path_parses_and_filters(tmp_path):
    def handler(request):
        assert request.url.params["token"] == "mykey"
        return httpx.Response(
            200,
            json={
                "economicCalendar": [
                    {
                        "event": "FOMC rate decision",
                        "time": "2026-12-10 19:00:00",
                        "impact": "high",
                        "country": "US",
                        "estimate": "5.50",
                        "prev": "5.50",
                    },
                    {
                        "event": "Fuera de horizonte",
                        "time": "2027-01-15 00:00:00",
                        "impact": "high",
                        "country": "US",
                    },
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="https://finnhub.io")
    cal = FinnhubEconomicCalendar(
        api_key="mykey",
        yaml_path=tmp_path / "unused.yml",
        http_client=http,
        env={},
    )
    rows = cal.events_within(hours=24, now=NOW)
    assert [r.event for r in rows] == ["FOMC rate decision"]
    assert rows[0].source == "finnhub"
    assert rows[0].forecast == "5.50"
    assert rows[0].region == "US"
