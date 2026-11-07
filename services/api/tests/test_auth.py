"""Validación del JWT de Supabase: HS256 con secreto y ES256 con JWKS."""

from __future__ import annotations

import time
import uuid
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from tm_api.auth import AuthError, JWTVerifier
from tm_api.settings import Settings

SECRET = "super-secret-jwt-token-with-at-least-32-characters-long"
USER = uuid.UUID("11111111-1111-4111-8111-111111111111")


def _claims(**overrides):
    now = int(time.time())
    base = {"sub": str(USER), "role": "authenticated", "aud": "authenticated", "exp": now + 60}
    base.update(overrides)
    return base


class _FakeJWKS:
    def __init__(self, public_key, fail: bool = False) -> None:
        self.public_key = public_key
        self.fail = fail
        self.calls = 0

    def get_signing_key_from_jwt(self, token: str):
        self.calls += 1
        if self.fail:
            raise jwt.PyJWKClientError("kid desconocido")
        return SimpleNamespace(key=self.public_key)


def test_hs256_valido():
    verifier = JWTVerifier(Settings(supabase_jwt_secret=SECRET))
    user = verifier.current_user(jwt.encode(_claims(email="a@b.c"), SECRET, algorithm="HS256"))
    assert user.id == USER
    assert user.email == "a@b.c"
    assert user.role == "authenticated"


@pytest.mark.parametrize(
    ("claims", "secret", "mensaje"),
    [
        (_claims(exp=int(time.time()) - 10), SECRET, "expiró"),
        (_claims(), "otro-secreto-de-al-menos-treinta-y-dos-caracteres", "inválido"),
        (_claims(aud="otra"), SECRET, "inválido"),
        (_claims(role="anon"), SECRET, "autenticado"),
        (_claims(sub="no-es-uuid"), SECRET, "UUID"),
    ],
)
def test_hs256_rechazos(claims, secret, mensaje):
    verifier = JWTVerifier(Settings(supabase_jwt_secret=SECRET))
    with pytest.raises(AuthError, match=mensaje):
        verifier.current_user(jwt.encode(claims, secret, algorithm="HS256"))


def test_hs256_sin_secreto_configurado():
    verifier = JWTVerifier(Settings(supabase_jwt_secret=""))
    with pytest.raises(AuthError, match="SUPABASE_JWT_SECRET"):
        verifier.verify(jwt.encode(_claims(), SECRET, algorithm="HS256"))


def test_token_mal_formado_y_alg_no_soportado():
    verifier = JWTVerifier(Settings(supabase_jwt_secret=SECRET))
    with pytest.raises(AuthError, match="mal formado"):
        verifier.verify("esto-no-es-un-jwt")
    hs384 = jwt.encode(_claims(), SECRET, algorithm="HS384")
    with pytest.raises(AuthError, match="no soportado"):
        verifier.verify(hs384)


def test_issuer_se_verifica_si_se_configura():
    verifier = JWTVerifier(Settings(supabase_jwt_secret=SECRET, jwt_issuer="http://x/auth/v1"))
    ok = jwt.encode(_claims(iss="http://x/auth/v1"), SECRET, algorithm="HS256")
    assert verifier.verify(ok)["iss"] == "http://x/auth/v1"
    with pytest.raises(AuthError):
        verifier.verify(jwt.encode(_claims(iss="http://otro"), SECRET, algorithm="HS256"))


def test_es256_con_jwks():
    private = ec.generate_private_key(ec.SECP256R1())
    jwks = _FakeJWKS(private.public_key())
    verifier = JWTVerifier(Settings(), jwks_client=jwks)
    token = jwt.encode(_claims(), private, algorithm="ES256", headers={"kid": "k1"})
    assert verifier.current_user(token).id == USER
    assert jwks.calls == 1


def test_es256_llave_desconocida():
    private = ec.generate_private_key(ec.SECP256R1())
    verifier = JWTVerifier(Settings(), jwks_client=_FakeJWKS(private.public_key(), fail=True))
    token = jwt.encode(_claims(), private, algorithm="ES256", headers={"kid": "k9"})
    with pytest.raises(AuthError, match="llave pública"):
        verifier.verify(token)


def test_es256_firma_de_otra_llave():
    private = ec.generate_private_key(ec.SECP256R1())
    other = ec.generate_private_key(ec.SECP256R1())
    verifier = JWTVerifier(Settings(), jwks_client=_FakeJWKS(other.public_key()))
    token = jwt.encode(_claims(), private, algorithm="ES256")
    with pytest.raises(AuthError, match="inválido"):
        verifier.verify(token)


def test_cliente_jwks_por_defecto_apunta_a_supabase():
    verifier = JWTVerifier(Settings(supabase_url="http://127.0.0.1:54321/"))
    client = verifier._jwks()
    assert isinstance(client, jwt.PyJWKClient)
    assert client.uri == "http://127.0.0.1:54321/auth/v1/.well-known/jwks.json"
    assert verifier._jwks() is client


def test_endpoint_sin_token_o_con_token_invalido(client, token):
    r = client.get("/profiles")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    r = client.get("/profiles", headers={"Authorization": f"Bearer {token(exp_in=-5)}"})
    assert r.status_code == 401
    assert "expiró" in r.json()["detail"]
    assert "invalid_token" in r.headers["www-authenticate"]
