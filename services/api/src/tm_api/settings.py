"""Configuración del servicio api, leída de variables de entorno.

Ninguna llave vive en el código: todo sale de `.env` (ver `.env.example`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

COMMANDS_STREAM = "tm:commands"
BACKTESTS_STREAM = "tm:backtests"
# Orígenes del frontend que pueden llamar a la API desde el navegador (CORS). El sitio es
# estático y llama a la API directamente, así que sin esto el navegador bloquea las llamadas.
DEFAULT_CORS_ORIGINS = ("http://localhost:3000", "http://127.0.0.1:3000")


def _origins(raw: str | None) -> tuple[str, ...]:
    if raw is None:
        return DEFAULT_CORS_ORIGINS
    return tuple(o.strip().rstrip("/") for o in raw.split(",") if o.strip())


@dataclass(frozen=True)
class Settings:
    """Parámetros de conexión a Supabase y Redis."""

    supabase_url: str = "http://127.0.0.1:54321"
    # Llave pública (anon o publishable) que Kong exige en el header `apikey`.
    supabase_anon_key: str = ""
    # Llave service_role (o sb_secret_). Solo la usa el alta de usuarios (POST /admin/users);
    # sin ella esa ruta responde 503 y todo lo demás funciona igual.
    supabase_service_role_key: str = ""
    # Secreto para validar JWT HS256. Si se deja vacío, solo se aceptan tokens asimétricos
    # (ES256/RS256) validados contra el JWKS de Supabase Auth.
    supabase_jwt_secret: str = ""
    jwt_audience: str = "authenticated"
    # Emisor esperado (claim `iss`). Vacío = no se verifica, porque dentro de Docker la URL
    # (host.docker.internal) no coincide con la que firma el token (127.0.0.1).
    jwt_issuer: str = ""
    redis_url: str = "redis://localhost:6379/0"
    commands_stream: str = COMMANDS_STREAM
    backtests_stream: str = BACKTESTS_STREAM
    # Tope aproximado de entradas por stream (XADD MAXLEN ~).
    stream_maxlen: int = 10_000
    # Reportes del laboratorio (JSON de `python -m tm_engine.lab`). Vacío = los del repo
    # (docs/estrategias/reportes); en Docker se monta ese directorio y se indica aquí.
    lab_reports_dir: str = ""
    # Orígenes permitidos por CORS (API_CORS_ORIGINS, separados por comas).
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS

    @property
    def rest_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/rest/v1"

    @property
    def jwks_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ
        return cls(
            supabase_url=env.get("SUPABASE_URL", cls.supabase_url),
            supabase_anon_key=env.get("SUPABASE_ANON_KEY")
            or env.get("SUPABASE_PUBLISHABLE_KEY", ""),
            supabase_service_role_key=env.get("SUPABASE_SERVICE_ROLE_KEY")
            or env.get("SUPABASE_SECRET_KEY", ""),
            supabase_jwt_secret=env.get("SUPABASE_JWT_SECRET", ""),
            jwt_audience=env.get("SUPABASE_JWT_AUDIENCE", cls.jwt_audience),
            jwt_issuer=env.get("SUPABASE_JWT_ISSUER", ""),
            redis_url=env.get("REDIS_URL", cls.redis_url),
            commands_stream=env.get("TM_COMMANDS_STREAM", COMMANDS_STREAM),
            backtests_stream=env.get("TM_BACKTESTS_STREAM", BACKTESTS_STREAM),
            lab_reports_dir=env.get("LAB_REPORTS_DIR", ""),
            cors_origins=_origins(env.get("API_CORS_ORIGINS")),
        )
