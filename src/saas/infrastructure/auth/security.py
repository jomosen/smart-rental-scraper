"""Auth primitives: password hashing, session JWTs, cookie config.

Security invariants (see docs/DATA_MODEL.md "Authentication: email + password"):
  - Passwords are stored only as a bcrypt hash; the raw password is never
    persisted or logged.
  - Session JWTs are HS256, signed with JWT_SECRET, carrying user_id + tenant_id
    + session_version, expiring in 30 days. They live in an httpOnly cookie.
  - Bumping users.session_version invalidates every outstanding JWT for that
    user (the value is embedded in the token and re-checked on each request).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import os
import secrets
import uuid

import bcrypt
import jwt

# ── Constants ────────────────────────────────────────────────────────────────
COOKIE_NAME = "rr_session"
TOKEN_TTL = _dt.timedelta(minutes=15)  # legacy magic-link / reset-token TTL
SESSION_TTL = _dt.timedelta(days=30)
_TOKEN_BYTES = 32  # 32 bytes = 256 bits
# bcrypt silently truncates input past 72 bytes; reject longer to avoid the
# false sense that the tail of a long passphrase contributes to security.
_MAX_PASSWORD_BYTES = 72


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


# ── Passwords ────────────────────────────────────────────────────────────────

def hash_password(raw: str) -> str:
    """Return a bcrypt hash (utf-8 str) of the password. Never store the raw."""
    _check_password_length(raw)
    return bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(raw: str, password_hash: str | None) -> bool:
    """Constant-time check of a candidate password against the stored hash.

    Returns False for a user with no password set (password_hash is None).
    """
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(raw.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def _check_password_length(raw: str) -> None:
    if len(raw.encode("utf-8")) > _MAX_PASSWORD_BYTES:
        raise ValueError(
            f"Password must be at most {_MAX_PASSWORD_BYTES} bytes (bcrypt limit)."
        )


# ── Magic-link / reset tokens (retained, see docs/DATA_MODEL.md) ─────────────

def generate_token() -> tuple[str, str]:
    """Return (raw_token, token_hash). Persist only the hash; email the raw."""
    raw = secrets.token_urlsafe(_TOKEN_BYTES)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── API keys (machine-to-machine, public prices API) ─────────────────────────
API_KEY_PREFIX = "rr_live_"
_API_KEY_BYTES = 32  # 256 bits of entropy


def generate_api_key() -> tuple[str, str, str]:
    """Return (raw_key, key_prefix, key_hash).

    Persist only key_prefix (for display) + key_hash; show the raw key once.
    Hashing reuses sha256 (hash_token): the key is a high-entropy random token,
    so a fast hash is appropriate (unlike user passwords, which need bcrypt).
    """
    raw = API_KEY_PREFIX + secrets.token_urlsafe(_API_KEY_BYTES)
    return raw, raw[:16], hash_token(raw)


# ── Session JWT ──────────────────────────────────────────────────────────────

def make_session_jwt(
    user_id: uuid.UUID, tenant_id: uuid.UUID, email: str, session_version: int = 0
) -> str:
    now = _dt.datetime.now(_dt.timezone.utc)
    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "email": email,
        "sv": session_version,
        "iat": now,
        "exp": now + SESSION_TTL,
    }
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def decode_session_jwt(token: str) -> dict:
    """Return claims, or raise jwt.PyJWTError if invalid/expired."""
    return jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
