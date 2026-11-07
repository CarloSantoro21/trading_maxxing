"""Autenticación con el JWT de Supabase Auth.

Con la CLI de Supabase 2.117 (GoTrue 2.19x) el stack local genera un par de llaves EC y
firma los `access_token` de los usuarios con **ES256**; la llave pública se publica en
`<SUPABASE_URL>/auth/v1/.well-known/jwks.json` (se comprobó con `GOTRUE_JWT_KEYS` y el
JWKS de un stack local). El secreto compartido (`GOTRUE_JWT_SECRET`, el que muestra
`supabase status`) sigue existiendo para las llaves *legacy* anon/service_role y para
proyectos que aún firman con **HS256**.

Por eso el verificador decide por el `alg` del encabezado del token:

* `ES256`/`RS256`/`EdDSA` -> se valida contra el JWKS (con caché, ver `PyJWKClient`).
* `HS256` -> se valida con `SUPABASE_JWT_SECRET`.
"""

from __future__ import annotations

from typing import Annotated, Any, Protocol
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from tm_api.settings import Settings

ASYMMETRIC_ALGORITHMS = frozenset({"ES256", "RS256", "EdDSA"})


class AuthError(Exception):
    """Token ausente, mal formado, expirado o con firma inválida."""


class SigningKeySource(Protocol):
    """Origen de llaves públicas (lo cumple `jwt.PyJWKClient`)."""

    def get_signing_key_from_jwt(self, token: str) -> Any: ...


class CurrentUser(BaseModel):
    """Usuario autenticado. `token` se reenvía a PostgREST para que aplique RLS."""

    id: UUID
    email: str | None = None
    role: str
    token: str


class JWTVerifier:
    """Valida tokens de Supabase con HS256 (secreto) o con el JWKS (asimétrico)."""

    def __init__(self, settings: Settings, jwks_client: SigningKeySource | None = None) -> None:
        self._settings = settings
        self._jwks_client = jwks_client

    def _jwks(self) -> SigningKeySource:
        if self._jwks_client is None:
            # PyJWKClient guarda en caché las llaves; solo consulta la red la primera vez
            # o cuando aparece un `kid` desconocido.
            self._jwks_client = jwt.PyJWKClient(self._settings.jwks_url, cache_keys=True)
        return self._jwks_client

    def verify(self, token: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthError("token mal formado") from exc
        alg = header.get("alg")
        if alg == "HS256":
            if not self._settings.supabase_jwt_secret:
                raise AuthError("SUPABASE_JWT_SECRET no está configurado para tokens HS256")
            key: Any = self._settings.supabase_jwt_secret
        elif alg in ASYMMETRIC_ALGORITHMS:
            try:
                key = self._jwks().get_signing_key_from_jwt(token).key
            except jwt.PyJWTError as exc:
                raise AuthError(f"no se encontró la llave pública del token: {exc}") from exc
        else:
            raise AuthError(f"algoritmo de firma no soportado: {alg}")

        options: dict[str, Any] = {"require": ["exp", "sub"]}
        kwargs: dict[str, Any] = {"audience": self._settings.jwt_audience}
        if self._settings.jwt_issuer:
            kwargs["issuer"] = self._settings.jwt_issuer
        try:
            claims = jwt.decode(token, key, algorithms=[alg], options=options, **kwargs)
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("el token expiró") from exc
        except jwt.PyJWTError as exc:
            raise AuthError(f"token inválido: {exc}") from exc
        if claims.get("role") != "authenticated":
            raise AuthError("el token no pertenece a un usuario autenticado")
        return claims

    def current_user(self, token: str) -> CurrentUser:
        claims = self.verify(token)
        try:
            user_id = UUID(str(claims["sub"]))
        except ValueError as exc:
            raise AuthError("el claim sub no es un UUID") from exc
        return CurrentUser(id=user_id, email=claims.get("email"), role=claims["role"], token=token)


_bearer = HTTPBearer(auto_error=False, description="JWT de Supabase Auth (access_token)")


def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser:
    """Dependencia de FastAPI: exige `Authorization: Bearer <jwt>` válido.

    Es síncrona a propósito: FastAPI la corre en un hilo, así la consulta del JWKS
    (solo la primera vez) no bloquea el event loop.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Falta el header Authorization: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )
    verifier: JWTVerifier = request.app.state.verifier
    try:
        return verifier.current_user(credentials.credentials)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"No autorizado: {exc}",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        ) from exc


User = Annotated[CurrentUser, Depends(get_current_user)]
