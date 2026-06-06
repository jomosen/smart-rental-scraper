"""Homegrown magic-link auth endpoints.

All DB access runs as smart_rental_super (BYPASSRLS + owner-inherited): these
operations are pre-auth and cross-tenant (resolve an email to its user/tenant,
read/write login_tokens which has no RLS). See docs/DATA_MODEL.md.
"""
from __future__ import annotations

import datetime as _dt

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import Engine, text

from src.saas.infrastructure.auth.email import send_magic_link
from src.saas.infrastructure.auth.security import (
    COOKIE_NAME,
    SESSION_TTL,
    TOKEN_TTL,
    cookie_secure,
    generate_token,
    hash_token,
    make_session_jwt,
)
from src.saas.infrastructure.persistence.engine import super_engine
from src.saas.infrastructure.persistence.session import super_session

from ..dependencies import get_current_user

router = APIRouter(prefix="/api/auth")

# Rate-limit windows (counted over login_tokens.created_at).
_RL_WINDOW = _dt.timedelta(minutes=15)
_RL_MAX_PER_EMAIL = 5
_RL_MAX_PER_IP = 20

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


@router.post("/request-link")
async def request_link(request: Request) -> JSONResponse:
    """Always returns an identical 200 (no account enumeration)."""
    ok = JSONResponse({"ok": True})
    try:
        body = await request.json()
    except Exception:
        body = {}
    email = str(body.get("email", "")).strip().lower()
    if not email or "@" not in email:
        return ok  # invalid input — same neutral response

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
            return ok  # throttled — stay silent

        user = s.execute(
            text("SELECT id FROM users WHERE LOWER(email) = :e LIMIT 1"),
            {"e": email},
        ).fetchone()

        raw, token_hash = generate_token()
        s.execute(
            text("""
                INSERT INTO login_tokens (email, user_id, token_hash, request_ip, expires_at)
                VALUES (:email, :user_id, :token_hash, :ip, :expires_at)
            """),
            {
                "email": email,
                "user_id": user.id if user else None,
                "token_hash": token_hash,
                "ip": ip,
                "expires_at": _now() + TOKEN_TTL,
            },
        )

    # Only email a usable link when the address maps to a real user. The token
    # for an unknown email is stored (for rate-limiting) but never sent and never
    # verifiable to a session (verify requires user_id).
    if user:
        import os
        base = os.environ.get("APP_BASE_URL", "http://localhost:8000").rstrip("/")
        send_magic_link(email, f"{base}/auth/verify?token={raw}")

    return ok


@router.get("/verify", response_model=None)
def verify(token: str = "") -> Response:
    if not token:
        return JSONResponse({"error": "Missing token"}, status_code=400)

    token_hash = hash_token(token)
    with super_session(_get_engine()) as s:
        row = s.execute(
            text("""
                SELECT id, user_id, expires_at, used_at
                FROM login_tokens WHERE token_hash = :h
            """),
            {"h": token_hash},
        ).fetchone()

        if row is None or row.user_id is None:
            return JSONResponse({"error": "Invalid token"}, status_code=400)
        if row.used_at is not None:
            return JSONResponse({"error": "Token already used"}, status_code=400)
        if row.expires_at < _now():
            return JSONResponse({"error": "Token expired"}, status_code=400)

        # Single-use: claim it atomically; if no row updated, it was just used.
        claimed = s.execute(
            text("UPDATE login_tokens SET used_at = NOW() WHERE id = :id AND used_at IS NULL"),
            {"id": row.id},
        )
        if claimed.rowcount == 0:
            return JSONResponse({"error": "Token already used"}, status_code=400)

        user = s.execute(
            text("SELECT id, email, tenant_id FROM users WHERE id = :id"),
            {"id": row.user_id},
        ).fetchone()
        if user is None:
            return JSONResponse({"error": "Invalid token"}, status_code=400)

    jwt_token = make_session_jwt(user.id, user.tenant_id, user.email)
    resp = RedirectResponse(url="/", status_code=303)
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
