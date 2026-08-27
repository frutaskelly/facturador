"""Authentication — verify Supabase JWTs against the project JWKS.

Supabase signs access tokens with an asymmetric key (ES256 for this project).
We fetch the public keys from the JWKS endpoint and verify the signature
locally — no shared secret lives in the backend, and verification is offline
after the first key fetch (PyJWKClient caches the keyset).

Trust boundary: the ONLY thing a request can prove is "I am auth user <sub>".
Tenant/role authorization is derived server-side from that identity
(see app/api/deps.py + app/core/rbac.py), never from request headers.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError

from .config import settings

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)

# Supabase always issues access tokens with this audience.
_EXPECTED_AUD = "authenticated"
_ALGORITHMS = ["ES256", "RS256"]


@lru_cache
def _jwks_client() -> PyJWKClient:
    url = settings.jwks_url()
    if not url:
        raise RuntimeError("SUPABASE_URL / SUPABASE_JWKS_URL not configured")
    # PyJWKClient caches keys in-process; lifespan refresh handles rotation.
    return PyJWKClient(url, cache_keys=True, lifespan=3600)


@dataclass(frozen=True)
class Principal:
    """An authenticated identity. Carries NO tenant/role authority by itself."""

    auth_user_id: str        # JWT `sub`
    email: Optional[str]
    role: Optional[str]      # Supabase role claim ("authenticated")
    claims: dict             # full verified claim set


def verify_token(token: str) -> dict:
    """Verify a Supabase access token and return its claims. Raises 401."""
    issuer = f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1" if settings.SUPABASE_URL else None
    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=_ALGORITHMS,
            audience=_EXPECTED_AUD,
            issuer=issuer,
            # Absorb minor client/server clock skew so a token issued a second
            # ago isn't rejected as "not yet valid" (iat) — applies to iat/nbf/exp.
            leeway=60,
            options={"require": ["exp", "sub"], "verify_iss": bool(issuer)},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expirado")
    except jwt.InvalidTokenError as e:
        logger.warning("JWT inválido: %s", e)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")
    except PyJWKClientError as e:
        # Could not resolve a signing key (unknown kid, or JWKS unreachable).
        # Deny by default — never fall back to an unverified token.
        logger.error("No se pudo resolver la signing key (JWKS): %s", e)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token no verificable")
    except RuntimeError as e:
        logger.error("Auth no configurado: %s", e)
        raise HTTPException(status_code=500, detail="Backend mal configurado (JWKS)")


# Prefijo de las claves de conexión (ver models/conexion.py). Se reconoce aquí
# para NO intentar verificarlas contra el JWKS: no son JWT y el error sería
# «token inválido», que no le dice nada a quien pegó una clave vencida.
CLAVE_CONEXION_PREFIJO = "fi_ss_"


def get_principal(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Principal:
    """FastAPI dependency: require a valid bearer token, return the Principal."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization: Bearer <jwt> requerido",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials.strip()
    if token.startswith(CLAVE_CONEXION_PREFIJO):
        # Clave de un sistema externo. La identidad NO se resuelve aquí: rbac la
        # busca en `conexiones` y de ahí saca el inquilino y su alcance. El
        # identificador es el hash, nunca el texto — así la clave no termina en
        # una llave de caché ni en un log.
        import hashlib

        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return Principal(
            auth_user_id=f"conexion:{digest}",
            email=None,
            role="conexion",
            claims={"conexion_hash": digest},
        )
    claims = verify_token(token)
    return Principal(
        auth_user_id=claims["sub"],
        email=claims.get("email"),
        role=claims.get("role"),
        claims=claims,
    )


def get_principal_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Optional[Principal]:
    if credentials is None or not credentials.credentials:
        return None
    try:
        return get_principal(credentials)
    except HTTPException:
        return None
