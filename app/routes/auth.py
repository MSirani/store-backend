"""Two account journeys; compatibility endpoints remain hidden from Swagger."""
import secrets
import sqlite3
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from ..db import get_connection
from ..schemas import LoginInput, RegisterInput, RegisterWithRoleInput
from ..security import bearer, current_user, hash_password, token_digest, verify_password

router = APIRouter(prefix="/auth", tags=["Auth"])
SIGNUP_BONUS_TOMAN = 100_000


def _register(data: RegisterInput, account_type: str):
    try:
        with get_connection(write=True) as conn:
            cursor = conn.execute(
                """INSERT INTO users(name, email, password_hash, account_type)
                   VALUES (?, ?, ?, ?)""",
                (data.name.strip(), str(data.email).lower(), hash_password(data.password), account_type),
            )
            user_id = cursor.lastrowid
            conn.execute("INSERT INTO carts(user_id) VALUES (?)", (user_id,))
            wallet = conn.execute(
                "INSERT INTO wallets(user_id, balance_toman) VALUES (?, ?)",
                (user_id, SIGNUP_BONUS_TOMAN),
            )
            conn.execute("""
                INSERT INTO wallet_transactions(wallet_id, kind, amount_toman, event_key)
                VALUES (?, 'signup_bonus', ?, ?)
            """, (wallet.lastrowid, SIGNUP_BONUS_TOMAN, f"signup:{user_id}"))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "Email already registered") from exc
    return {"id": user_id, "name": data.name.strip(), "email": str(data.email).lower(),
            "account_type": account_type, "signup_bonus_toman": SIGNUP_BONUS_TOMAN}


@router.post("/register/customer", status_code=201)
def register_customer(data: RegisterInput):
    return _register(data, "customer")


@router.post("/register/seller", status_code=201)
def register_seller(data: RegisterInput):
    return _register(data, "seller")


@router.post("/register", status_code=201, include_in_schema=False)
def register_legacy(data: RegisterWithRoleInput):
    # Retained for existing clients; only customer or seller may self-register.
    return _register(data, data.account_type)


def _login(data: LoginInput, expected_type: str | None):
    with get_connection() as conn:
        row = conn.execute("""
            SELECT id, password_hash, account_type, role
              FROM users WHERE email = ?
        """, (str(data.email).lower(),)).fetchone()
    # The same generic message avoids exposing whether an account exists or its type.
    if row is None or not verify_password(data.password, row["password_hash"]):
        raise HTTPException(401, "Invalid email, password or account type")
    actual_type = "admin" if row["role"] == "admin" else row["account_type"]
    if expected_type is not None and actual_type != expected_type:
        raise HTTPException(401, "Invalid email, password or account type")
    token = secrets.token_urlsafe(32)
    expires_at = int(time.time()) + 7 * 24 * 60 * 60
    with get_connection(write=True) as conn:
        conn.execute("INSERT INTO sessions(token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                     (token_digest(token), row["id"], expires_at))
    return {"access_token": token, "token_type": "bearer", "expires_at": expires_at,
            "account_type": actual_type}


@router.post("/login/customer")
def login_customer(data: LoginInput):
    return _login(data, "customer")


@router.post("/login/seller")
def login_seller(data: LoginInput):
    return _login(data, "seller")


@router.post("/login/admin")
def login_admin(data: LoginInput):
    # Administrators cannot sign up as admin. Promote only via local trusted script.
    return _login(data, "admin")


@router.post("/login", include_in_schema=False)
def login_legacy(data: LoginInput):
    # Backward compatibility for old clients / scripts. Prefer type-specific routes.
    return _login(data, None)


@router.get("/me")
def me(user: dict = Depends(current_user)):
    return user


@router.post("/logout", status_code=204)
def logout(credentials: HTTPAuthorizationCredentials = Depends(bearer),
           _user: dict = Depends(current_user)):
    with get_connection(write=True) as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_digest(credentials.credentials),))
    return None
