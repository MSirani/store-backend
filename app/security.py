"""Scrypt password hashes, hashed bearer sessions, and authenticated-user lookup."""
import base64
import hashlib
import hmac
import secrets
import time

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .db import get_connection

bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, salt_b64, hash_b64 = stored.split("$")
        if algorithm != "scrypt":
            return False
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(hash_b64, validate=True)
        actual = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    if credentials is None:
        raise HTTPException(401, "Missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    with get_connection() as conn:
        user = conn.execute("""
            SELECT u.id, u.name, u.email,
                   CASE WHEN u.role='admin' THEN 'admin' ELSE u.account_type END AS role,
                   sh.id AS shop_id,
                   CASE WHEN u.role = 'admin' THEN 'admin'
                        ELSE u.account_type END AS account_type
              FROM sessions AS s
              JOIN users AS u ON u.id = s.user_id
              LEFT JOIN shops AS sh ON sh.user_id = u.id
             WHERE s.token_hash = ? AND s.expires_at > ?
        """, (token_digest(credentials.credentials), int(time.time()))).fetchone()
    if user is None:
        raise HTTPException(401, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
    return dict(user)


def require_admin(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(403, "Admin access required")
    return user
