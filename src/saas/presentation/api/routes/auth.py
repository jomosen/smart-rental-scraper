"""Homegrown email + password auth endpoints.

All DB access runs as smart_rental_super (BYPASSRLS + owner-inherited): these
operations are pre-auth and cross-tenant (resolve an email to its user/tenant,
read/write login_tokens which has no RLS). See docs/DATA_MODEL.md
§"Authentication: email + password".

login_tokens is retained but no longer drives login; here it backs only the
per-email / per-IP rate-limit window for the login endpoint.
"""
from __future__ import annotations

import datetime as _dt

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, text

from src.saas.infrastructure.auth.security import (
    COOKIE_NAME,
    SESSION_TTL,
    cookie_secure,
    make_session_jwt,
    verify_password,
)
from src.saas.infrastructure.persistence.engine import super_engine
from src.saas.infrastructure.persistence.session import super_session

from ..dependencies import get_current_user

router = APIRouter(prefix="/api/auth")

# Rate-limit windows (counted over login_tokens.created_at).
_RL_WINDOW = _dt.timedelta(minutes=15)
_RL_MAX_PER_EMAIL = 10
_RL_MAX_PER_IP = 40

_engine: Engine | None = None


def _get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = super_engine()
    return _engine


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _set_session_cookie(resp, token: str) -> None:
    resp.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
        path="/",
    )


def _record_attempt(s, email: str, ip: str | None) -> None:
    """Log a login attempt in login_tokens so the rate-limit window can count it.

    No token is minted here; token_hash is a unique sentinel per attempt.
    """
    import secrets as _secrets

    s.execute(
        text("""
            INSERT INTO login_tokens (email, token_hash, request_ip, expires_at)
            VALUES (:email, :token_hash, :ip, :expires_at)
        """),
        {
            "email": email,
            "token_hash": _secrets.token_hex(32),  # 64 hex chars, just a marker
            "ip": ip,
            "expires_at": _now() + _RL_WINDOW,
        },
    )


@router.post("/login", response_model=None)
async def login(request: Request) -> JSONResponse:
    """Email + password login. Generic 401 on any failure (no enumeration)."""
    invalid = JSONResponse({"error": "Invalid credentials"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        body = {}
    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", ""))
    if not email or not password:
        return invalid

    ip = request.client.host if request.client else None
    since = _now() - _RL_WINDOW

    with super_session(_get_engine()) as s:
        # Rate limit per email and per IP within the window.
        email_count = s.execute(
            text("SELECT COUNT(*) FROM login_tokens WHERE email = :e AND created_at > :since"),
            {"e": email, "since": since},
        ).scalar() or 0
        ip_count = (
            s.execute(
                text("SELECT COUNT(*) FROM login_tokens WHERE request_ip = :ip AND created_at > :since"),
                {"ip": ip, "since": since},
            ).scalar() or 0
        ) if ip else 0
        if email_count >= _RL_MAX_PER_EMAIL or ip_count >= _RL_MAX_PER_IP:
            return JSONResponse(
                {"error": "Too many attempts. Try again later."}, status_code=429
            )

        _record_attempt(s, email, ip)

        user = s.execute(
            text("""
                SELECT id, tenant_id, email, password_hash, session_version
                FROM users WHERE LOWER(email) = :e LIMIT 1
            """),
            {"e": email},
        ).fetchone()

    # Verify outside the session; verify_password is constant-time and handles
    # the no-password (None hash) case. A missing user still pays the hash cost
    # via a dummy compare to keep timing uniform.
    if user is None:
        verify_password(password, None)
        return invalid
    if not verify_password(password, user.password_hash):
        return invalid

    jwt_token = make_session_jwt(
        user.id, user.tenant_id, user.email, user.session_version
    )
    resp = JSONResponse({"ok": True})
    _set_session_cookie(resp, jwt_token)
    return resp


@router.get("/me")
def me(claims: dict = Depends(get_current_user)) -> dict:
    with super_session(_get_engine()) as s:
        name = s.execute(
            text("SELECT name FROM tenants WHERE id = :id"),
            {"id": claims["tenant_id"]},
        ).scalar()
    return {"email": claims.get("email"), "tenant_name": name or ""}


@router.post("/logout")
def logout() -> JSONResponse:
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(key=COOKIE_NAME, path="/")
    return resp
