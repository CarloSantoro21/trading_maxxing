"""Logging estructurado JSON para stdout (consumido por Azure App Service → Log Analytics /
Application Insights, ver #29).

Azure App Service toma cada línea de stdout del contenedor y la envía al workspace de Log
Analytics asociado; si la línea es un JSON válido, Log Analytics / Application Insights la
indexan como campos y permiten filtrar por `level`, `service` o cualquier extra sin regex. La
wiring completa de APM (`azure-monitor-opentelemetry` con `configure_azure_monitor()`) vive
en #29 — aquí solo nos aseguramos de que el stdout del motor sea JSON estructurado. Este
helper es idempotente: si ya hay un handler JSON instalado no se duplica (útil cuando
`__main__` y las pruebas vuelven a llamar).

Uso típico (`__main__.py`):

    from tm_engine.logging_setup import setup_json_logging
    setup_json_logging(service="engine", level=os.environ.get("LOG_LEVEL", "INFO"))

Los logs quedan como:

    {"ts": "2026-11-17T18:00:00.000Z", "level": "INFO", "service": "engine",
     "logger": "tm_engine.service", "message": "..."}

Campos extra: se pasan con `logger.info(msg, extra={"symbol": "BTCUSDT"})` y aparecen en la
raíz del JSON. Si el `record` trae `exc_info`, se añade `exception` con el traceback.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

# Claves estándar de LogRecord que no son campos "extra" del usuario.
_RESERVED = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


class JsonFormatter(logging.Formatter):
    """Formatea cada `LogRecord` como una línea JSON lista para Log Analytics / App Insights."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Campos extra pasados por `logger.info(..., extra={...})`.
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            payload[key] = _safe(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=_safe, ensure_ascii=False)


def _safe(value: Any) -> Any:
    """Convierte valores no serializables (Decimal, datetime, etc.) a algo que `json` acepta."""
    try:
        json.dumps(value)
    except TypeError:
        return str(value)
    return value


def setup_json_logging(
    service: str = "engine",
    level: str = "INFO",
    stream: Any | None = None,
) -> logging.Handler:
    """Instala un handler JSON en el logger raíz; es idempotente.

    Se reemplazan los handlers de stdout que no sean JSON para evitar líneas duplicadas con
    distinto formato (p. ej. el handler por defecto que puso Nautilus al importar). El handler
    devuelto es útil para las pruebas.
    """
    root = logging.getLogger()
    root.setLevel(level.upper())
    formatter = JsonFormatter(service=service)
    # Si ya tenemos un handler JSON instalado, no lo duplicamos.
    for handler in root.handlers:
        if isinstance(getattr(handler, "formatter", None), JsonFormatter):
            handler.setFormatter(formatter)
            handler.setLevel(level.upper())
            return handler
    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(formatter)
    handler.setLevel(level.upper())
    root.addHandler(handler)
    return handler
