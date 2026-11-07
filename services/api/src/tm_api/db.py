"""Acceso a datos a través de PostgREST (Supabase), actuando en nombre del usuario.

Cada llamada reenvía el JWT del usuario en `Authorization`, así PostgreSQL evalúa las
políticas de RLS con `auth.uid()` y la API nunca ve filas ajenas. Ningún endpoint de
este servicio necesita la llave service_role: las migraciones permiten al rol
`authenticated` leer y escribir perfiles, metas y bots, leer trades y equity, y encolar
backtests en estado `queued`.

Los filtros siguen la sintaxis de PostgREST (`{"id": "eq.<uuid>", "closed_at": "is.null"}`).
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx
from fastapi import HTTPException, status

Row = dict[str, Any]
Params = dict[str, str]

# 422 literal: el nombre de la constante cambió entre versiones de Starlette.
UNPROCESSABLE = 422


class Database(Protocol):
    """Operaciones que usan los routers; la implementan PostgREST y el doble de pruebas."""

    async def select(self, table: str, token: str, params: Params | None = None) -> list[Row]: ...

    async def select_page(
        self, table: str, token: str, params: Params, *, limit: int, offset: int
    ) -> tuple[list[Row], int]: ...

    async def insert(self, table: str, token: str, row: Row) -> Row: ...

    async def update(self, table: str, token: str, filters: Params, values: Row) -> list[Row]: ...

    async def delete(self, table: str, token: str, filters: Params) -> list[Row]: ...


# Códigos de error de PostgreSQL/PostgREST -> (status HTTP, mensaje para el cliente).
_PG_ERRORS: dict[str, tuple[int, str]] = {
    "23514": (UNPROCESSABLE, "Los datos no cumplen una regla de la base"),
    "23505": (status.HTTP_409_CONFLICT, "Ya existe un registro con esos datos"),
    "23503": (UNPROCESSABLE, "El registro referenciado no existe"),
    "23502": (UNPROCESSABLE, "Falta un campo obligatorio"),
    "22P02": (UNPROCESSABLE, "Valor con formato inválido"),
    "42501": (status.HTTP_403_FORBIDDEN, "No tienes permiso sobre este recurso"),
    "PGRST116": (status.HTTP_404_NOT_FOUND, "Recurso no encontrado"),
    "PGRST301": (status.HTTP_401_UNAUTHORIZED, "PostgREST rechazó el token"),
    "PGRST303": (status.HTTP_401_UNAUTHORIZED, "PostgREST rechazó el token"),
}


def _raise_for_postgrest(response: httpx.Response) -> None:
    if response.is_success:
        return
    try:
        body = response.json()
    except ValueError:
        body = {}
    code = str(body.get("code", "")) if isinstance(body, dict) else ""
    http_status, message = _PG_ERRORS.get(
        code, (status.HTTP_502_BAD_GATEWAY, "Error inesperado de la base de datos")
    )
    if response.status_code == status.HTTP_401_UNAUTHORIZED:
        http_status, message = status.HTTP_401_UNAUTHORIZED, "PostgREST rechazó el token"
    detail: dict[str, Any] = {"message": message, "code": code or None}
    if isinstance(body, dict) and body.get("message"):
        detail["db_message"] = body["message"]
    raise HTTPException(status_code=http_status, detail=detail)


def parse_content_range(value: str | None) -> int:
    """Total de filas de un header `Content-Range: 0-49/123` (o `*/0`)."""
    if not value or "/" not in value:
        return 0
    total = value.rsplit("/", 1)[1]
    return int(total) if total.isdigit() else 0


class PostgrestClient:
    """Cliente mínimo de PostgREST sobre `httpx.AsyncClient`."""

    def __init__(self, http: httpx.AsyncClient, rest_url: str, api_key: str) -> None:
        self._http = http
        self._rest_url = rest_url.rstrip("/")
        self._api_key = api_key

    def _headers(self, token: str, **extra: str) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if self._api_key:
            headers["apikey"] = self._api_key
        headers.update(extra)
        return headers

    def _url(self, table: str) -> str:
        return f"{self._rest_url}/{table}"

    async def select(self, table: str, token: str, params: Params | None = None) -> list[Row]:
        response = await self._http.get(
            self._url(table), params=params or {}, headers=self._headers(token)
        )
        _raise_for_postgrest(response)
        return response.json()

    async def select_page(
        self, table: str, token: str, params: Params, *, limit: int, offset: int
    ) -> tuple[list[Row], int]:
        query = {**params, "limit": str(limit), "offset": str(offset)}
        response = await self._http.get(
            self._url(table), params=query, headers=self._headers(token, Prefer="count=exact")
        )
        _raise_for_postgrest(response)
        return response.json(), parse_content_range(response.headers.get("content-range"))

    async def insert(self, table: str, token: str, row: Row) -> Row:
        response = await self._http.post(
            self._url(table),
            json=row,
            headers=self._headers(token, Prefer="return=representation"),
        )
        _raise_for_postgrest(response)
        rows = response.json()
        return rows[0]

    async def update(self, table: str, token: str, filters: Params, values: Row) -> list[Row]:
        response = await self._http.patch(
            self._url(table),
            params=filters,
            json=values,
            headers=self._headers(token, Prefer="return=representation"),
        )
        _raise_for_postgrest(response)
        return response.json()

    async def delete(self, table: str, token: str, filters: Params) -> list[Row]:
        response = await self._http.delete(
            self._url(table),
            params=filters,
            headers=self._headers(token, Prefer="return=representation"),
        )
        _raise_for_postgrest(response)
        return response.json()
