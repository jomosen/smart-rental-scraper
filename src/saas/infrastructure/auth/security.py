"""Auth primitives: magic-link tokens, session JWTs, cookie config.

Security invariants (see docs/DATA_MODEL.md "Authentication: minimal homegrown
magic link"):
  - Raw magic-link tokens are ≥256 bits of CSPRNG entropy and are NEVER stored;
    only their SHA-256 hex is persisted.
  - Session JWTs are HS256, signed with JWT_SECRET, carrying user_id + tenant_id,
    expiring in 7 days. They live in an httpOnly cookie.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import os
import secrets
import uuid

import jwt

# ── Constants ────────────────────────────────────────────────────────────────
COOKIE_NAME = "rr_session"
TOKEN_TTL = _dt.timedelta(minutes=15)
SESSION_TTL = _dt.timedelta(days=7)
_TOKEN_BYTES = 32  # 32 bytes = 256 bits


def _jwt_secret() -> str:
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise RuntimeError("JWT_SECRET is not set. Configure it in .env.")
    return secret


def app_env() -> str:
    return os.environ.get("APP_ENV", "development").lower()


def cookie_secure() -> bool:
    """Secure flag on except in local development (http loopback)."""
    return app_env() != "development"


# ── Magic-link tokens ────────────────────────────────────────────────────────

def generate_token() -> tuple[str, str]:
    """Return (raw_token, token_hash). Persist only the hash; email the raw."""
    raw = secrets.token_urlsafe(_TOKEN_BYTES)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── Session JWT ──────────────────────────────────────────────────────────────

def make_session_jwt(user_id: uuid.UUID, tenant_id: uuid.UUID, email: str) -> str:
    now = _dt.datetime.now(_dt.timezone.utc)
    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "email": email,
        "iat": now,
        "exp": now + SESSION_TTL,
    }
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def decode_session_jwt(token: str) -> dict:
    """Return claims, or raise jwt.PyJWTError if invalid/expired."""
    return jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
