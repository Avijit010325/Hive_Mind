import secrets
import time

import bcrypt
import jwt

from .config import settings
from .db import KV, SessionLocal

COOKIE = "hivemind_session"
SESSION_SECONDS = 30 * 24 * 3600
_secret: str | None = None


def secret() -> str:
    """SECRET_KEY from the environment, or one generated once and kept in the DB."""
    global _secret
    if _secret:
        return _secret
    if settings.secret_key:
        _secret = settings.secret_key
        return _secret
    with SessionLocal() as db:
        row = db.get(KV, "secret_key")
        if row is None:
            row = KV(key="secret_key", value=secrets.token_urlsafe(48))
            db.add(row)
            db.commit()
        _secret = row.value
    return _secret


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode()[:72], hashed.encode())
    except ValueError:
        return False


def make_session(user_id: int) -> str:
    payload = {"sub": str(user_id), "exp": int(time.time()) + SESSION_SECONDS}
    return jwt.encode(payload, secret(), algorithm="HS256")


def read_session(token: str) -> int | None:
    try:
        return int(jwt.decode(token, secret(), algorithms=["HS256"])["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None
