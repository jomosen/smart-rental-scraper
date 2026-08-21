"""Integration tests for the catalog API (/api/provider-groups).

Requires the local Postgres (docker compose up -d postgres) with migrations
applied. See tests/saas/conftest.py.

The endpoint feeds the group-matching selector, so what it must guarantee is:
one entry per logical group (not per provider_vehicle_categories row), a stable
`group_key` to persist, and unclassified groups included rather than filtered out.
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
def catalog_fixture():
    """A provider with two groups: one coded + classified, one code-less.

    Committed (the app opens its own connections) and torn down afterwards.
    """
    engine = super_engine()
    code = f"testprov{uuid.uuid4().hex[:8]}"
    email = f"catalog-test-{uuid.uuid4().hex[:12]}@example.com"
    password = "Sup3rSecret!"
    ids: dict = {"provider_code": code, "email": email, "password": password}

    with super_session(engine) as s:
        ids["tenant_id"] = s.execute(
            text("INSERT INTO tenants (name, currency, plan) "
                 "VALUES ('Catalog Tenant', 'EUR', 'mvp') RETURNING id")
        ).scalar()
        ids["user_id"] = s.execute(
            text("INSERT INTO users (tenant_id, email, password_hash, role) "
                 "VALUES (:t, :e, :h, 'owner') RETURNING id"),
            {"t": ids["tenant_id"], "e": email, "h": hash_password(password)},
        ).scalar()
        ids["provider_id"] = s.execute(
            text("INSERT INTO providers "
                 "(code, display_name, status, scraper_key, default_currency) "
                 "VALUES (:c, 'Catalog Test Provider', 'active', :c, 'EUR') "
                 "RETURNING id"),
            {"c": code},
        ).scalar()
        ids["location_id"] = s.execute(
            text("INSERT INTO provider_locations "
                 "(provider_id, location_code, location_name, country, city, active) "
                 "VALUES (:p, 'TSTLOC', 'Test Office', 'ES', 'Alicante', TRUE) "
                 "RETURNING id"),
            {"p": ids["provider_id"]},
        ).scalar()
        ids["rate_id"] = s.execute(
            text("INSERT INTO provider_rates (provider_id, rate_code, rate_name, active) "
                 "VALUES (:p, 'TSTRATE', 'Test Rate', TRUE) RETURNING id"),
            {"p": ids["provider_id"]},
        ).scalar()
        # Coded + classified as MDMR (the generated acriss_code column composes
        # the four attribute chars).
        s.execute(
            text("""
                INSERT INTO provider_vehicle_categories
                    (provider_id, provider_location_id, provider_rate_id,
                     external_code, external_name, example_models, transmission,
                     acriss_category, acriss_body_type, acriss_transmission,
                     acriss_fuel, active)
                VALUES (:p, :l, :r, 'Grupo TEST-A', 'Test A',
                        'FIAT PANDA, KIA PICANTO', 'manual',
                        'M', 'D', 'M', 'R', TRUE)
            """),
            {"p": ids["provider_id"], "l": ids["location_id"], "r": ids["rate_id"]},
        )
        # Code-less and unclassified: identity falls back to attributes_hash.
        s.execute(
            text("""
                INSERT INTO provider_vehicle_categories
                    (provider_id, provider_location_id, provider_rate_id,
                     external_code, attributes_hash, example_models, active)
                VALUES (:p, :l, :r, NULL, 'abc123def456789a', 'OPEL CORSA', TRUE)
            """),
            {"p": ids["provider_id"], "l": ids["location_id"], "r": ids["rate_id"]},
        )

    yield ids

    with super_session(engine) as s:
        s.execute(text("DELETE FROM provider_vehicle_categories WHERE provider_id = :p"),
                  {"p": ids["provider_id"]})
        s.execute(text("DELETE FROM provider_rates WHERE provider_id = :p"),
                  {"p": ids["provider_id"]})
        s.execute(text("DELETE FROM provider_locations WHERE provider_id = :p"),
                  {"p": ids["provider_id"]})
        s.execute(text("DELETE FROM providers WHERE id = :p"), {"p": ids["provider_id"]})
        s.execute(text("DELETE FROM users WHERE id = :u"), {"u": ids["user_id"]})
        s.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": ids["tenant_id"]})


def _login(client: TestClient, fixture: dict) -> None:
    resp = client.post(
        "/api/auth/login",
        json={"email": fixture["email"], "password": fixture["password"]},
    )
    assert resp.status_code == 200, resp.text


def _groups_of(payload: dict, provider_code: str) -> dict:
    return {
        g["group_key"]: g
        for g in payload["groups"]
        if g["provider_code"] == provider_code
    }


class TestProviderGroupsEndpoint:
    def test_requires_authentication_outside_development(
        self, client, catalog_fixture, monkeypatch
    ):
        """The dev bypass (DEV_TENANT_ID) is disabled outside development."""
        monkeypatch.setenv("APP_ENV", "production")
        assert client.get("/api/provider-groups").status_code == 401

    def test_returns_coded_group_with_models_split(self, client, catalog_fixture):
        _login(client, catalog_fixture)
        resp = client.get("/api/provider-groups")
        assert resp.status_code == 200, resp.text

        group = _groups_of(resp.json(), catalog_fixture["provider_code"])["Grupo TEST-A"]
        assert group["external_code"] == "Grupo TEST-A"
        assert group["attributes_hash"] is None
        assert group["acriss_code"] == "MDMR"
        assert group["transmission"] == "manual"
        # Free-text example_models is split into individual names for the picker.
        assert group["models"] == ["FIAT PANDA", "KIA PICANTO"]

    def test_includes_unclassified_codeless_group(self, client, catalog_fixture):
        """Group-to-group matching does not require an ACRISS classification."""
        _login(client, catalog_fixture)
        groups = _groups_of(
            client.get("/api/provider-groups").json(),
            catalog_fixture["provider_code"],
        )

        group = groups["abc123def456789a"]
        assert group["external_code"] is None
        # group_key falls back to the hash, so the entry is still addressable.
        assert group["attributes_hash"] == "abc123def456789a"
        assert group["acriss_code"] is None
        assert group["models"] == ["OPEL CORSA"]

    def test_one_entry_per_group_not_per_pvc_row(self, client, catalog_fixture):
        _login(client, catalog_fixture)
        groups = _groups_of(
            client.get("/api/provider-groups").json(),
            catalog_fixture["provider_code"],
        )
        assert set(groups) == {"Grupo TEST-A", "abc123def456789a"}

    def test_provider_filter_restricts_results(self, client, catalog_fixture):
        _login(client, catalog_fixture)
        payload = client.get(
            "/api/provider-groups",
            params={"provider": catalog_fixture["provider_code"]},
        ).json()

        assert payload["provider"] == catalog_fixture["provider_code"]
        assert payload["total"] == 2
        assert {g["provider_code"] for g in payload["groups"]} == {
            catalog_fixture["provider_code"]
        }

    def test_unknown_provider_returns_empty(self, client, catalog_fixture):
        _login(client, catalog_fixture)
        payload = client.get(
            "/api/provider-groups", params={"provider": "does-not-exist"}
        ).json()
        assert payload["total"] == 0
        assert payload["groups"] == []

    def test_unknown_location_is_404(self, client, catalog_fixture):
        _login(client, catalog_fixture)
        resp = client.get("/api/provider-groups", params={"location_id": 987654321})
        assert resp.status_code == 404
