import time
import jwt
import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from .config import ISSUER, OIDC_INTERNAL
from .db import Actor, SYSTEM, transaction, one

bearer = HTTPBearer(auto_error=False)
_jwks = None
_until = 0.0


def authenticate(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    request: Request = None,
) -> Actor:
    global _jwks, _until
    if not credentials:
        raise HTTPException(401, "Войдите через Keycloak")
    try:
        now = time.monotonic()
        if _jwks is None or now > _until:
            response = httpx.get(
                OIDC_INTERNAL + "/protocol/openid-connect/certs", timeout=5
            )
            response.raise_for_status()
            _jwks = jwt.PyJWKSet.from_dict(response.json())
            _until = now + 60
        header = jwt.get_unverified_header(credentials.credentials)
        key = next(k.key for k in _jwks.keys if k.key_id == header.get("kid"))
        claims = jwt.decode(
            credentials.credentials,
            key,
            algorithms=["RS256"],
            audience="prohod-api",
            issuer=ISSUER,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
    except (jwt.PyJWTError, StopIteration, ValueError):
        raise HTTPException(401, "Недействительный токен")
    except httpx.HTTPError:
        raise HTTPException(503, "Сервис авторизации недоступен")
    roles = frozenset(claims.get("realm_access", {}).get("roles", [])) & {
        "admin",
        "manager",
        "controller",
        "employee",
    }
    with transaction(SYSTEM) as conn:
        principal = one(
            conn,
            "SELECT * FROM principals WHERE issuer=%s AND subject=%s AND active",
            (ISSUER, claims["sub"]),
        )
    if not principal:
        raise HTTPException(403, "Учётная запись не зарегистрирована или заблокирована")
    actor = Actor(principal["id"], roles, principal["username"])
    if request is not None:
        request.state.actor = actor
    return actor
