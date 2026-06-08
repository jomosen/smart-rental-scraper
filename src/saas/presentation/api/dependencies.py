"""FastAPI dependencies — the auth seam.

`get_current_tenant` resolves the request's tenant from the session cookie
(a signed JWT). In development only, it falls back to DEV_TENANT_ID when there
is no cookie, so the API is usable locally without logging in. In production
(APP_ENV != development) the fallback is disabled and a missing/invalid session
yields 401.

`get_current_user` is stricter: it requires a valid session cookie (no dev
bypass) and is used by /api/auth/me, which must reflect only a real session.

Both validate the JWT's `sv` (session_version) against users.session_version:
a mismatch means the session was invalidated server-side (password change,
log-out-everywhere) and is rejected as 401, even though the JWT is otherwise
well-formed and unexpired.
"""
from __future__ import annotations

import os
import uuid

import jwt
from fastapi import HTTPException, Request
from sqlalchemy import Engine, text

from src.saas.infrastructure.auth.security import COOKIE_NAME, app_env, decode_session_jwt
from src.saas.infrastructure.persistence.engine import super_engine
from src.saas.infrastructure.persistence.session import super_session

_engine: Engine | None = None


def _get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = super_engine()
    return _engine


def _claims_from_cookie(request: Request) -> dict | None:
    """Decode + signature/expiry-check the cookie, then confirm session_version.

    Returns the claims for a live session, or None for any failure (no cookie,
    tampered/expired JWT, or a session invalidated via session_version bump).
    """
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        claims = decode_session_jwt(token)
    except jwt.PyJWTError:
        # Expired or tampered cookie → treat as no session.
        return None

    if not _session_version_ok(claims):
        return None
    return claims


def _session_version_ok(claims: dict) -> bool:
    """True when the JWT's `sv` matches the user's current session_version."""
    user_id = claims.get("sub")
    if not user_id:
        return False
    with super_session(_get_engine()) as s:
        current = s.execute(
            text("SELECT session_version FROM users WHERE id = :id"),
            {"id": user_id},
        ).scalar()
    if current is None:
        return False  # user deleted
    return int(current) == int(claims.get("sv", -1))


def get_current_tenant(request: Request) -> uuid.UUID:
    claims = _claims_from_cookie(request)
    if claims is not None:
        return uuid.UUID(claims["tenant_id"])

    # Dev-only bypass: no cookie + APP_ENV=development + DEV_TENANT_ID set.
    if app_env() == "development":
        dev = os.environ.get("DEV_TENANT_ID")
        if dev:
            return uuid.UUID(dev)

    raise HTTPException(status_code=401, detail={"error": "Not authenticated"})


def get_current_user(request: Request) -> dict:
    """Return JWT claims (sub, tenant_id, email) for a real session, else 401.

    No dev bypass — /api/auth/me must answer only for an actual cookie session.
    """
    claims = _claims_from_cookie(request)
    if claims is None:
        raise HTTPException(status_code=401, detail={"error": "Not authenticated"})
    return claims
