"""Alta de usuarios del equipo (issue #67).

El registro público está apagado (`enable_signup = false`): un admin da de alta a cada
persona con la API admin de Supabase Auth, que exige la llave service_role. Se usa desde
dos lugares:

* `POST /admin/users` (solo admin; la API necesita `SUPABASE_SERVICE_ROLE_KEY`).
* `make user EMAIL=... ROLE=...`, que corre este módulo como script::

      SUPABASE_URL=http://127.0.0.1:54321 SUPABASE_SERVICE_ROLE_KEY=... \\
        uv run --package tm-api python -m tm_api.users --email ana@equipo.dev --role member

El usuario se crea con el correo confirmado y una contraseña temporal, y su rol se guarda
en `public.app_roles` (upsert, así que repetir el comando con otro rol lo cambia).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import string
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx

from tm_api.schemas import AppRole

# Sin caracteres ambiguos (0/O, 1/l/I) para que se pueda dictar o copiar a mano.
_ALPHABET = "".join(c for c in string.ascii_letters + string.digits if c not in "0O1lI")
TEMP_PASSWORD_LENGTH = 16


class UserAdminError(Exception):
    """La API de Supabase rechazó la operación."""


class UserExistsError(UserAdminError):
    """Ya hay un usuario con ese correo."""


def generate_temporary_password(length: int = TEMP_PASSWORD_LENGTH) -> str:
    """Contraseña aleatoria con al menos una minúscula, una mayúscula y un dígito."""
    if length < 12:
        raise ValueError("la contraseña temporal debe tener al menos 12 caracteres")
    while True:
        password = "".join(secrets.choice(_ALPHABET) for _ in range(length))
        if (
            any(c.islower() for c in password)
            and any(c.isupper() for c in password)
            and any(c.isdigit() for c in password)
        ):
            return password


def service_headers(service_key: str) -> dict[str, str]:
    """Headers para Auth y PostgREST con la llave de servicio.

    Una llave legacy (JWT `eyJ...`) va también en `Authorization`; las `sb_secret_...` solo
    van en `apikey` (Kong la traduce).
    """
    headers = {"apikey": service_key}
    if service_key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {service_key}"
    return headers


@dataclass(frozen=True)
class NewUser:
    id: UUID
    email: str
    role: AppRole
    temporary_password: str


class UserAdmin:
    """Crea usuarios con la API admin de Supabase Auth y les asigna su rol."""

    def __init__(self, http: httpx.AsyncClient, supabase_url: str, service_key: str) -> None:
        if not service_key:
            raise ValueError("falta la llave service_role (SUPABASE_SERVICE_ROLE_KEY)")
        self._http = http
        self._base = supabase_url.rstrip("/")
        self._headers = service_headers(service_key)

    async def create_user(
        self, email: str, role: AppRole, *, password: str | None = None
    ) -> NewUser:
        password = password or generate_temporary_password()
        response = await self._http.post(
            f"{self._base}/auth/v1/admin/users",
            headers=self._headers,
            json={
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {"temporary_password": True},
            },
        )
        if response.status_code in (409, 422) and _is_email_exists(response):
            raise UserExistsError(f"ya existe un usuario con el correo {email}")
        if not response.is_success:
            raise UserAdminError(
                f"Supabase Auth respondió {response.status_code}: {_msg(response)}"
            )
        user_id = UUID(response.json()["id"])
        try:
            await self.set_role(user_id, role)
        except UserAdminError:
            # Sin rol el usuario sería member por omisión: mejor no dejarlo a medias.
            await self._http.delete(
                f"{self._base}/auth/v1/admin/users/{user_id}", headers=self._headers
            )
            raise
        return NewUser(id=user_id, email=email, role=role, temporary_password=password)

    async def set_role(self, user_id: UUID, role: AppRole) -> None:
        response = await self._http.post(
            f"{self._base}/rest/v1/app_roles",
            params={"on_conflict": "user_id"},
            headers={**self._headers, "Prefer": "resolution=merge-duplicates,return=minimal"},
            json={"user_id": str(user_id), "role": role.value},
        )
        if not response.is_success:
            raise UserAdminError(
                f"no se pudo guardar el rol ({response.status_code}): {_msg(response)}"
            )


def _body(response: httpx.Response) -> Mapping[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, Mapping) else {}


def _msg(response: httpx.Response) -> str:
    body = _body(response)
    return str(body.get("msg") or body.get("message") or body.get("error") or response.text)


def _is_email_exists(response: httpx.Response) -> bool:
    body = _body(response)
    code = str(body.get("error_code") or body.get("code") or "")
    return code == "email_exists" or "already been registered" in _msg(response)


# --- script (make user) -------------------------------------------------------


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m tm_api.users",
        description="Da de alta un usuario del equipo con contraseña temporal.",
    )
    parser.add_argument("--email", required=True)
    parser.add_argument("--role", choices=[r.value for r in AppRole], default=AppRole.MEMBER.value)
    return parser.parse_args(argv)


async def _create(
    email: str, role: AppRole, env: Mapping[str, str], transport: httpx.AsyncBaseTransport | None
) -> NewUser:
    url = env.get("SUPABASE_URL", "http://127.0.0.1:54321")
    key = env.get("SUPABASE_SERVICE_ROLE_KEY") or env.get("SUPABASE_SECRET_KEY", "")
    async with httpx.AsyncClient(timeout=10.0, transport=transport) as http:
        return await UserAdmin(http, url, key).create_user(email, role)


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> int:
    args = _parse_args(argv)
    email = args.email.strip().lower()
    try:
        user = asyncio.run(_create(email, AppRole(args.role), env or os.environ, transport))
    except (UserAdminError, ValueError, httpx.HTTPError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Usuario creado: {user.email} ({user.role.value}), id {user.id}")
    print(f"Contraseña temporal: {user.temporary_password}")
    print("Compártela por un canal privado; la persona debe cambiarla al entrar.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
