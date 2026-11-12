"""Servicio de autenticación.

Responsabilidad única: emitir y validar credenciales para los clientes de la
plataforma (app móvil, web, operadores). No conoce nada del dominio de voz o
señas. Los usuarios viven en la base propia del servicio (AUTH_DATABASE_URL,
ADR-014), con la contraseña derivada con scrypt y una sal por usuario; la primera
vez se siembran desde AUTH_USERS ("user:pass:rol1|rol2,user2:pass2:rol"). Sin
base configurada (pruebas, desarrollo) se validan contra esa variable.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time

import jwt
from fastapi import HTTPException, status
from pydantic import BaseModel

from rsp_common.auth import ALGORITHM, decode_token, secret
from rsp_common.db import Database
from rsp_common.service import Service

svc = Service("auth", needs_bus=False)
app = svc.app

TOKEN_TTL_S = int(os.getenv("AUTH_TOKEN_TTL_S", "3600"))


def _users() -> dict[str, dict]:
    raw = os.getenv("AUTH_USERS", "operator:operator123:operator,admin:admin123:admin|operator")
    users = {}
    for item in raw.split(","):
        parts = item.strip().split(":")
        if len(parts) != 3:
            continue
        user, pwd, roles = parts
        users[user] = {"password": pwd, "roles": roles.split("|")}
    return users


DB_URL = os.getenv("AUTH_DATABASE_URL")
db = Database(DB_URL) if DB_URL else None


def _hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1).hex()


def init_db() -> None:
    """Crea la tabla de usuarios y, si está vacía, la siembra desde AUTH_USERS."""
    with db.conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY, salt TEXT NOT NULL, password_hash TEXT NOT NULL, roles TEXT NOT NULL)""")
        if c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0:
            for name, u in _users().items():
                salt = os.urandom(16)
                c.execute("INSERT INTO users (username, salt, password_hash, roles) VALUES (?, ?, ?, ?)",
                          (name, salt.hex(), _hash(u["password"], salt), "|".join(u["roles"])))


def authenticate(username: str, password: str) -> list[str] | None:
    """Roles del usuario si la contraseña es correcta; None si no."""
    if db is None:
        user = _users().get(username)
        return user["roles"] if user and hmac.compare_digest(user["password"], password) else None
    with db.conn() as c:
        row = c.execute("SELECT salt, password_hash, roles FROM users WHERE username = ?", (username,)).fetchone()
    # el mismo trabajo exista o no el usuario: el tiempo de respuesta no revela qué nombres existen
    digest = _hash(password, bytes.fromhex(row["salt"]) if row else b"usuario-inexistente")
    if row and hmac.compare_digest(row["password_hash"], digest):
        return row["roles"].split("|")
    return None


@svc.on_startup
async def start():
    if db is not None:
        init_db()
        svc.log.info("usuarios en la base propia del servicio")


class TokenRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


@app.post("/token", response_model=TokenResponse, tags=["auth"])
def issue_token(req: TokenRequest):  # síncrono: FastAPI lo ejecuta en un hilo, y scrypt y la base no bloquean el bucle de eventos
    roles = authenticate(req.username, req.password)
    if roles is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "credenciales inválidas")
    now = int(time.time())
    payload = {"sub": req.username, "roles": roles, "iat": now, "exp": now + TOKEN_TTL_S}
    token = jwt.encode(payload, secret(), algorithm=ALGORITHM)
    return TokenResponse(access_token=token, expires_in=TOKEN_TTL_S)


class IntrospectRequest(BaseModel):
    token: str


@app.post("/introspect", tags=["auth"])
async def introspect(req: IntrospectRequest):
    try:
        claims = decode_token(req.token)
        return {"active": True, **claims}
    except jwt.PyJWTError as exc:
        return {"active": False, "error": str(exc)}
