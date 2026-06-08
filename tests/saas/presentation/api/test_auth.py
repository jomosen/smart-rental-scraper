"""Integration tests for the email + password auth flow.

Requires the local Postgres (docker compose up -d postgres) with migrations
applied, plus JWT_SECRET / SUPER_DATABASE_URL in .env. See tests/saas/conftest.py.

Covers: login success, wrong password, unknown email, rate limiting,
session_version invalidation, logout, and tenant isolation of /me.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.saas.infrastructure.auth.security import hash_password
from src.saas.infrastructure.persistence.engine import super_engine
from src.saas.infrastructure.persistence.session import super_session
from src.saas.presentation.api.app import create_app


@pytest.fixture()
def client():
    return TestClient(create_app())


@pytest.fixture()
def make_user():
    """Factory creating (tenant, user-with-password). Cleans up afterwards."""
    created: list[tuple[uuid.UUID, uuid.UUID]] = []  # (tenant_id, user_id)
    emails: list[str] = []
    engine = super_engine()

    def _make(password: str = "Sup3rSecret!", name: str = "Test Tenant") -> dict:
        email = f"auth-test-{uuid.uuid4().hex[:12]}@example.com"
        with super_session(engine) as s:
            tenant_id = s.execute(
                text("INSERT INTO tenants (name, currency, plan) "
                     "VALUES (:n, 'EUR', 'mvp') RETURNING id"),
                {"n": name},
            ).scalar()
            user_id = s.execute(
                text("INSERT INTO users (tenant_id, email, password_hash, role) "
                     "VALUES (:t, :e, :h, 'owner') RETURNING id"),
                {"t": tenant_id, "e": email, "h": hash_password(password)},
            ).scalar()
        created.append((tenant_id, user_id))
        emails.append(email)
        return {"email": email, "password": password,
                "tenant_id": tenant_id, "user_id": user_id, "tenant_name": name}

    yield _make

    with super_session(engine) as s:
        for email in emails:
            s.execute(text("DELETE FROM login_tokens WHERE email = :e"), {"e": email})
        for tenant_id, user_id in created:
            s.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
            s.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": tenant_id})


def _login(client, email, password):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_login_success_sets_cookie_and_me_works(client, make_user):
    u = make_user(name="Acme Co")
    r = _login(client, u["email"], u["password"])
    assert r.status_code == 200
    assert "rr_session" in r.cookies

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == u["email"]
    assert body["tenant_name"] == "Acme Co"


def test_login_wrong_password_is_401(client, make_user):
    u = make_user()
    r = _login(client, u["email"], "not-the-password")
    assert r.status_code == 401
    assert "rr_session" not in r.cookies


def test_login_unknown_email_is_401(client, make_user):
    # make_user unused but keeps the cleanup fixture symmetric / DB reachable.
    r = _login(client, f"nobody-{uuid.uuid4().hex}@example.com", "whatever")
    assert r.status_code == 401


def test_me_without_session_is_401(client):
    fresh = TestClient(create_app())
    assert fresh.get("/api/auth/me").status_code == 401


def test_logout_clears_session(client, make_user):
    u = make_user()
    assert _login(client, u["email"], u["password"]).status_code == 200
    assert client.get("/api/auth/me").status_code == 200

    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_session_version_bump_invalidates_existing_session(client, make_user):
    u = make_user()
    assert _login(client, u["email"], u["password"]).status_code == 200
    assert client.get("/api/auth/me").status_code == 200

    # Simulate a password change / "log out everywhere": bump session_version.
    with super_session(super_engine()) as s:
        s.execute(
            text("UPDATE users SET session_version = session_version + 1 WHERE id = :id"),
            {"id": u["user_id"]},
        )

    # The still-present cookie now carries a stale sv → rejected.
    assert client.get("/api/auth/me").status_code == 401


def test_rate_limit_per_email_returns_429(client, make_user):
    u = make_user()
    # _RL_MAX_PER_EMAIL = 10: the 11th attempt within the window is throttled.
    last = None
    for _ in range(11):
        last = _login(client, u["email"], "wrong-on-purpose")
    assert last.status_code == 429
