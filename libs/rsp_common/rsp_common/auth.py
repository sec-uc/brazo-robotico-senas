"""Verificación de tokens JWT compartida (HS256 en el prototipo).

El servicio `auth` emite tokens; el gateway (y cualquier servicio que exponga
API pública) los verifica con este módulo. Se usa un secreto compartido
(HS256) para mantener el prototipo simple; migrar a RS256/JWKS o a Keycloak
solo cambia `decode_token` (ADR-006).
"""
from __future__ import annotations

import os

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

ALGORITHM = "HS256"
_bearer = HTTPBearer(auto_error=False)


def secret() -> str:
    """Clave HS256. Sin valor por defecto: una clave conocida permitiría firmar tokens de cualquiera."""
    s = os.getenv("AUTH_SECRET", "")
    if len(s.encode()) < 32:  # RFC 7518 §3.2: al menos 256 bits para HS256
        raise RuntimeError("AUTH_SECRET debe definirse con al menos 32 bytes")
    return s


def auth_enabled() -> bool:
    return os.getenv("AUTH_ENABLED", "true").lower() in ("1", "true", "yes")


def decode_token(token: str) -> dict:
    return jwt.decode(token, secret(), algorithms=[ALGORITHM])


def has_role(user: dict, *roles: str) -> bool:
    return bool(set(user.get("roles") or []) & set(roles))


def require_role(*roles: str):
    """Dependencia FastAPI: token válido Y alguno de `roles` (si no, 403). ADR-011."""
    async def dep(user: dict = Depends(require_user)) -> dict:
        if not has_role(user, *roles):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"se requiere el rol {' o '.join(roles)}")
        return user
    return dep


async def require_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> dict:
    if not auth_enabled():
        return {"sub": "anonymous", "roles": ["operator"]}
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token requerido")
    try:
        return decode_token(creds.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"token inválido: {exc}") from exc
