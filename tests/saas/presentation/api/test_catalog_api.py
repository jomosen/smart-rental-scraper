"""Integration tests for the provider-groups catalog API (/api/v1/provider-groups).

Requires the local Postgres (docker compose up -d postgres) with migrations
applied. See tests/saas/conftest.py.

The endpoint feeds an external system's matching selector, so what it must
guarantee is: API-key auth like the rest of /api/v1, one entry per logical
group with a stable `group_key`, unclassified groups included, and a coverage
object whose day counts distinguish backed seasons from empty ones and vary
per duration.
"""
from __future__ import annotations

import datetime
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.saas.infrastructure.auth.security import generate_api_key
from src.saas.infrastructure.persistence.engine import super_engine
from src.saas.infrastructure.persistence.session import super_session
from src.saas.presentation.api.app import create_app

# Observations are written into the May 2026 partition, which the test DB is
# known to have (same convention as test_price_query_service). pickup_date is
# what coverage reads; observed_at only routes the partition.
_OBSERVED_AT = datetime.datetime(2026, 5, 1, 10, 0, 0)

TODAY = date.today()
# Season 1: started before today, still current, backed at 7d only.
Z1_START, Z1_END = TODAY - timedelta(days=3), TODAY + timedelta(days=10)
# Season 2: future, no observation at all.
Z2_START, Z2_END = TODAY + timedelta(days=11), TODAY + timedelta(days=24)


@pytest.fixture()
def client():
    return TestClient(create_app())


@pytest.fixture()
def catalog_fixture():
    """A provider with two groups, seasons, and one backed observation.

    - Grupo TEST-A: coded, classified MDMR, two seasons ahead — the first
      backed by a 7-day observation, the second empty.
    - One code-less, unclassified group (identity = attributes_hash), no seasons.

    Committed (the app opens its own connections) and torn down afterwards.
    """
    engine = super_engine()
    code = f"testprov{uuid.uuid4().hex[:8]}"
    ids: dict = {"provider_code": code}

    with super_session(engine) as s:
        ids["tenant_id"] = s.execute(
            text("INSERT INTO tenants (name, currency, plan) "
                 "VALUES ('Catalog Tenant', 'EUR', 'mvp') RETURNING id")
        ).scalar()
        raw_key, prefix, key_hash = generate_api_key()
        ids["raw_key"] = raw_key
        s.execute(
            text("INSERT INTO api_keys (tenant_id, name, key_prefix, key_hash) "
                 "VALUES (:t, 'catalog-test', :p, :h)"),
            {"t": ids["tenant_id"], "p": prefix, "h": key_hash},
        )
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
        ids["pvc_id"] = s.execute(
            text("""
                INSERT INTO provider_vehicle_categories
                    (provider_id, provider_location_id, provider_rate_id,
                     external_code, external_name, example_models, transmission,
                     acriss_category, acriss_body_type, acriss_transmission,
                     acriss_fuel, active)
                VALUES (:p, :l, :r, 'Grupo TEST-A', 'Test A',
                        'FIAT PANDA, KIA PICANTO', 'manual',
                        'M', 'D', 'M', 'R', TRUE)
                RETURNING id
            """),
            {"p": ids["provider_id"], "l": ids["location_id"], "r": ids["rate_id"]},
        ).scalar()
        s.execute(
            text("""
                INSERT INTO provider_vehicle_categories
                    (provider_id, provider_location_id, provider_rate_id,
                     external_code, attributes_hash, example_models, active)
                VALUES (:p, :l, :r, NULL, 'abc123def456789a', 'OPEL CORSA', TRUE)
            """),
            {"p": ids["provider_id"], "l": ids["location_id"], "r": ids["rate_id"]},
        )
        for start, end in ((Z1_START, Z1_END), (Z2_START, Z2_END)):
            s.execute(
                text("""
                    INSERT INTO homogeneous_zones
                        (provider_id, provider_location_id, provider_rate_id,
                         provider_vehicle_category_id, start_date, end_date,
                         representative_date, active)
                    VALUES (:p, :l, :r, :v, :s, :e, :s, TRUE)
                """),
                {"p": ids["provider_id"], "l": ids["location_id"],
                 "r": ids["rate_id"], "v": ids["pvc_id"], "s": start, "e": end},
            )
        run_id = s.execute(
            text("INSERT INTO scrape_runs "
                 "(provider_id, provider_location_id, provider_rate_id, status) "
                 "VALUES (:p, :l, :r, 'success') RETURNING id"),
            {"p": ids["provider_id"], "l": ids["location_id"], "r": ids["rate_id"]},
        ).scalar()
        s.execute(
            text("""
                INSERT INTO price_observations
                    (provider_id, provider_location_id, provider_rate_id,
                     provider_vehicle_category_id, scrape_run_id, pickup_date,
                     duration_days, price_per_day, total_price, currency, observed_at)
                VALUES (:p, :l, :r, :v, :run, :pickup, 7, :ppd, :total, 'EUR', :at)
            """),
            {"p": ids["provider_id"], "l": ids["location_id"], "r": ids["rate_id"],
             "v": ids["pvc_id"], "run": run_id,
             "pickup": Z1_START + timedelta(days=1),
             "ppd": Decimal("34.14"), "total": Decimal("239.00"),
             "at": _OBSERVED_AT},
        )

    yield ids

    with super_session(engine) as s:
        for sql in (
            "DELETE FROM price_observations WHERE provider_id = :p",
            "DELETE FROM homogeneous_zones WHERE provider_id = :p",
            "DELETE FROM scrape_runs WHERE provider_id = :p",
            "DELETE FROM provider_vehicle_categories WHERE provider_id = :p",
            "DELETE FROM provider_rates WHERE provider_id = :p",
            "DELETE FROM provider_locations WHERE provider_id = :p",
            "DELETE FROM providers WHERE id = :p",
        ):
            s.execute(text(sql), {"p": ids["provider_id"]})
        s.execute(text("DELETE FROM api_keys WHERE tenant_id = :t"),
                  {"t": ids["tenant_id"]})
        s.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": ids["tenant_id"]})


def _get(client, fixture, **params):
    return client.get(
        "/api/v1/provider-groups",
        params=params,
        headers={"Authorization": f"Bearer {fixture['raw_key']}"},
    )


def _groups_of(payload: dict, provider_code: str) -> dict:
    return {
        g["group_key"]: g
        for g in payload["groups"]
        if g["provider_code"] == provider_code
    }


class TestAuth:
    def test_missing_key_returns_401(self, client):
        assert client.get("/api/v1/provider-groups").status_code == 401

    def test_invalid_key_returns_401(self, client):
        resp = client.get(
            "/api/v1/provider-groups",
            headers={"Authorization": "Bearer rr_live_does_not_exist"},
        )
        assert resp.status_code == 401


class TestCatalog:
    def test_coded_group_with_models_split(self, client, catalog_fixture):
        resp = _get(client, catalog_fixture)
        assert resp.status_code == 200, resp.text

        group = _groups_of(resp.json(), catalog_fixture["provider_code"])["Grupo TEST-A"]
        assert group["external_code"] == "Grupo TEST-A"
        assert group["attributes_hash"] is None
        assert group["acriss_code"] == "MDMR"
        assert group["transmission"] == "manual"
        assert group["models"] == ["FIAT PANDA", "KIA PICANTO"]

    def test_includes_unclassified_codeless_group(self, client, catalog_fixture):
        """Group-to-group matching does not require an ACRISS classification."""
        groups = _groups_of(
            _get(client, catalog_fixture).json(), catalog_fixture["provider_code"]
        )
        group = groups["abc123def456789a"]
        assert group["external_code"] is None
        assert group["attributes_hash"] == "abc123def456789a"
        assert group["acriss_code"] is None
        # No seasons at all → the empty-coverage shape, not a missing key.
        assert group["coverage"]["covered_days"] == 0
        assert group["coverage"]["ranges"] == []
        assert group["coverage"]["through"] is None

    def test_provider_filter_and_unknown_location(self, client, catalog_fixture):
        payload = _get(client, catalog_fixture,
                       provider=catalog_fixture["provider_code"]).json()
        assert payload["total"] == 2
        assert {g["provider_code"] for g in payload["groups"]} == {
            catalog_fixture["provider_code"]
        }
        assert _get(client, catalog_fixture, location_id=987654321).status_code == 404

    def test_invalid_duration_is_422(self, client, catalog_fixture):
        assert _get(client, catalog_fixture, duration=9).status_code == 422


class TestCoverage:
    def test_backed_and_empty_seasons_are_distinguished(self, client, catalog_fixture):
        group = _groups_of(
            _get(client, catalog_fixture).json(), catalog_fixture["provider_code"]
        )["Grupo TEST-A"]
        cov = group["coverage"]

        # Two seasons ahead; the current one clipped to start today.
        assert [r["priced"] for r in cov["ranges"]] == [True, False]
        assert cov["ranges"][0]["from"] == TODAY.isoformat()
        assert cov["ranges"][0]["through"] == Z1_END.isoformat()
        assert cov["ranges"][1]["from"] == Z2_START.isoformat()

        # covered_days counts only the backed season (clipped, inclusive);
        # horizon stops at the last backed day, not the last season.
        expected_covered = (Z1_END - TODAY).days + 1
        assert cov["covered_days"] == expected_covered
        assert cov["through"] == Z1_END.isoformat()
        assert cov["horizon_days"] == expected_covered
        assert cov["last_observed_at"] is not None

    def test_coverage_varies_per_duration(self, client, catalog_fixture):
        """The observation is 7-day only: coverage at 7d > 0, at 1d = 0."""
        group = _groups_of(
            _get(client, catalog_fixture).json(), catalog_fixture["provider_code"]
        )["Grupo TEST-A"]
        by_dur = group["coverage"]["by_duration"]

        assert by_dur["7"] > 0
        assert by_dur["1"] == 0
        assert by_dur["28"] == 0

    def test_reference_duration_changes_the_calendar(self, client, catalog_fixture):
        """duration=1 → no season is backed, ranges all unpriced, zero days."""
        group = _groups_of(
            _get(client, catalog_fixture, duration=1).json(),
            catalog_fixture["provider_code"],
        )["Grupo TEST-A"]
        cov = group["coverage"]

        assert [r["priced"] for r in cov["ranges"]] == [False, False]
        assert cov["covered_days"] == 0
        assert cov["through"] is None
        assert cov["horizon_days"] == 0
