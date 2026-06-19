"""Tests for the public prices API (/api/v1/prices) and its API-key auth.

Layers covered:
  - generate_api_key crypto contract (pure unit, no DB).
  - api_keys lookup contract used by get_tenant_from_api_key (super session,
    rollback-isolated).
  - HTTP-level auth + tenant isolation via TestClient. These commit two tenants
    + keys (the app opens its own connections, so data must be visible) and clean
    up in finally — mirrors TestTenantIsolation in test_repositories.py.
"""
from __future__ import annotations

import datetime

from fastapi.testclient import TestClient
from sqlalchemy import delete, text

from src.saas.infrastructure.auth.security import (
    API_KEY_PREFIX,
    generate_api_key,
    hash_token,
)
from src.saas.infrastructure.persistence.models.tenant import ApiKey, Tenant
from src.saas.presentation.api.app import create_app

_LOOKUP_SQL = text(
    "SELECT tenant_id FROM api_keys WHERE key_hash = :h AND revoked_at IS NULL"
)


class TestGenerateApiKey:
    def test_hash_and_prefix_match(self):
        raw, prefix, h = generate_api_key()
        assert raw.startswith(API_KEY_PREFIX)
        assert prefix == raw[:16]
        assert h == hash_token(raw)
        assert len(h) == 64  # sha256 hex digest

    def test_keys_are_unique(self):
        assert generate_api_key()[0] != generate_api_key()[0]


class TestApiKeyLookup:
    """The exact lookup get_tenant_from_api_key runs (super, BYPASSRLS)."""

    def test_active_key_resolves_tenant(self, super_db_session):
        t = Tenant(name="K Tenant", currency="EUR")
        super_db_session.add(t)
        super_db_session.flush()
        _, prefix, h = generate_api_key()
        super_db_session.add(
            ApiKey(tenant_id=t.id, name="k", key_prefix=prefix, key_hash=h)
        )
        super_db_session.flush()

        row = super_db_session.execute(_LOOKUP_SQL, {"h": h}).fetchone()
        assert row is not None
        assert row.tenant_id == t.id

    def test_revoked_key_not_resolved(self, super_db_session):
        t = Tenant(name="K Tenant", currency="EUR")
        super_db_session.add(t)
        super_db_session.flush()
        _, prefix, h = generate_api_key()
        super_db_session.add(
            ApiKey(
                tenant_id=t.id,
                name="k",
                key_prefix=prefix,
                key_hash=h,
                revoked_at=datetime.datetime.now(datetime.timezone.utc),
            )
        )
        super_db_session.flush()

        assert super_db_session.execute(_LOOKUP_SQL, {"h": h}).fetchone() is None

    def test_unknown_key_not_resolved(self, super_db_session):
        _, _, h = generate_api_key()
        assert super_db_session.execute(_LOOKUP_SQL, {"h": h}).fetchone() is None


class TestPublicPricesApiHttp:
    def test_missing_key_returns_401(self):
        client = TestClient(create_app())
        assert client.get("/api/v1/prices").status_code == 401

    def test_invalid_key_returns_401(self):
        client = TestClient(create_app())
        resp = client.get(
            "/api/v1/prices",
            headers={"Authorization": "Bearer rr_live_does_not_exist"},
        )
        assert resp.status_code == 401

    def test_valid_key_resolves_own_tenant_and_isolates(self, super_db_session):
        """Each key resolves to its own tenant; revoking a key revokes access."""
        ids: list = []
        try:
            ta = Tenant(name="Tenant Alpha", currency="EUR")
            tb = Tenant(name="Tenant Beta", currency="USD")
            super_db_session.add_all([ta, tb])
            super_db_session.flush()
            raw_a, pa, ha = generate_api_key()
            raw_b, pb, hb = generate_api_key()
            super_db_session.add_all([
                ApiKey(tenant_id=ta.id, name="a", key_prefix=pa, key_hash=ha),
                ApiKey(tenant_id=tb.id, name="b", key_prefix=pb, key_hash=hb),
            ])
            super_db_session.commit()
            ids = [ta.id, tb.id]

            client = TestClient(create_app())
            resp_a = client.get(
                "/api/v1/prices", headers={"Authorization": f"Bearer {raw_a}"}
            )
            resp_b = client.get(
                "/api/v1/prices", headers={"Authorization": f"Bearer {raw_b}"}
            )

            assert resp_a.status_code == 200
            assert resp_b.status_code == 200
            # The resolved tenant is reflected in the payload — each key sees only
            # its own tenant (RLS-scoped read of the tenants table).
            assert resp_a.json()["tenant"] == "Tenant Alpha"
            assert resp_a.json()["currency"] == "EUR"
            assert resp_b.json()["tenant"] == "Tenant Beta"
            assert resp_b.json()["currency"] == "USD"

            # Revoking key A immediately denies access.
            super_db_session.execute(
                text("UPDATE api_keys SET revoked_at = NOW() WHERE key_hash = :h"),
                {"h": ha},
            )
            super_db_session.commit()
            resp_a_revoked = client.get(
                "/api/v1/prices", headers={"Authorization": f"Bearer {raw_a}"}
            )
            assert resp_a_revoked.status_code == 401
        finally:
            if ids:
                super_db_session.execute(delete(ApiKey).where(ApiKey.tenant_id.in_(ids)))
                super_db_session.execute(delete(Tenant).where(Tenant.id.in_(ids)))
                super_db_session.commit()
