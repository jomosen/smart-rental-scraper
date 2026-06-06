"""FastAPI dependencies — the auth seam.

`get_current_tenant` resolves the request's tenant from the session cookie
(a signed JWT). In development only, it falls back to DEV_TENANT_ID when there
is no cookie, so the API is usable locally without logging in. In production
(APP_ENV != development) the fallback is disabled and a missing/invalid session
yields 401.

`get_current_user` is stricter: it requires a valid session cookie (no dev
bypass) and is used by /api/auth/me, which must reflect only a real session.
"""
from __future__ import annotations

import os
import uuid

import jwt
from fastapi import HTTPException, Request

from src.saas.infrastructure.auth.security import COOKIE_NAME, app_env, decode_session_jwt


def _claims_from_cookie(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        return decode_session_jwt(token)
    except jwt.PyJWTError:
        # Expired or tampered cookie → treat as no session.
        return None


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
