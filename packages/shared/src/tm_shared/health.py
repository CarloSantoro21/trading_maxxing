"""Respuesta estándar de healthcheck para todos los servicios."""

from pydantic import BaseModel

from tm_shared import __version__


class Health(BaseModel):
    service: str
    status: str = "ok"
    version: str = __version__


def health(service: str) -> Health:
    return Health(service=service)
