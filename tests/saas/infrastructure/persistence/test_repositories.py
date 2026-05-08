"""Integration tests for SaaS persistence repositories.

Each test is self-contained: it inserts the data it needs via super_db_session
(BYPASSRLS, so tenant-table inserts work without app.tenant_id being set),
then exercises the repository under test.

All changes are rolled back by the conftest fixtures after each test,
except TestTenantIsolation which must commit cross-session and cleans up
explicitly in a try/finally block.
"""
from __future__ import annotations

import datetime
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, text

from src.saas.infrastructure.persistence.models.catalog import (
    HomogeneousZone,
    Provider,
    ProviderLocation,
    ProviderRate,
    ProviderVehicleGroup,
    ScrapeRun,
)
from src.saas.infrastructure.persistence.models.tenant import Tenant, User
from src.saas.infrastructure.persistence.repositories import (
    HomogeneousZoneRepository,
    PriceObservationRepository,
    ProviderLocationRepository,
    ProviderRateRepository,
    ProviderRepository,
    ProviderVehicleGroupRepository,
    ScrapeRunRepository,
)


# ---------------------------------------------------------------------------
# Fixture helpers — column names match migration 616af4ee21c5
# ---------------------------------------------------------------------------

def _provider(session, code: str = "test_provider_a") -> Provider:
    p = Provider(
        code=code,
        display_name="Test Provider",
        scraper_key="provider_a",
        default_currency="EUR",
        status="active",
    )
    session.add(p)
    session.flush()
    return p


def _location(session, provider_id: int, location_code: str = "LOC1") -> ProviderLocation:
    loc = ProviderLocation(
        provider_id=provider_id,
        location_code=location_code,
        location_name="Test Location",
        country="ES",
        city="Barcelona",
        active=True,
    )
    session.add(loc)
    session.flush()
    return loc


def _rate(session, provider_id: int, rate_code: str = "RATE1") -> ProviderRate:
    rate = ProviderRate(
        provider_id=provider_id,
        rate_code=rate_code,
        rate_name="Test Rate",
        active=True,
    )
    session.add(rate)
    session.flush()
    return rate


def _vehicle_group(
    session,
    provider_id: int,
    provider_location_id: int,
    provider_rate_id: int,
    external_code: str = "ECONOMY",
) -> ProviderVehicleGroup:
    vg = ProviderVehicleGroup(
        provider_id=provider_id,
        provider_location_id=provider_location_id,
        provider_rate_id=provider_rate_id,
        external_code=external_code,
        external_name="Economy",
        active=True,
    )
    session.add(vg)
    session.flush()
    return vg


def _tenant(session) -> Tenant:
    t = Tenant(name="Test Tenant", currency="EUR")
    session.add(t)
    session.flush()
    return t


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestProviderRepository:
    def test_get_by_code_found(self, super_db_session):
        _provider(super_db_session, code="find_me")
        repo = ProviderRepository(super_db_session)
        result = repo.get_by_code("find_me")
        assert result is not None
        assert result.code == "find_me"

    def test_get_by_code_missing(self, super_db_session):
        repo = ProviderRepository(super_db_session)
        assert repo.get_by_code("no_such_code") is None

    def test_list_active_filters_status(self, super_db_session):
        _provider(super_db_session, code="active_one")
        p = Provider(
            code="broken_one",
            display_name="Broken",
            scraper_key="provider_b",
            default_currency="USD",
            status="broken",
        )
        super_db_session.add(p)
        super_db_session.flush()
        repo = ProviderRepository(super_db_session)
        codes = {r.code for r in repo.list_active()}
        assert "active_one" in codes
        assert "broken_one" not in codes


class TestProviderVehicleGroupRepository:
    def test_upsert_seen_inserts_new(self, super_db_session):
        p = _provider(super_db_session, code="vg_insert_test")
        loc = _location(super_db_session, p.id)
        rate = _rate(super_db_session, p.id)
        repo = ProviderVehicleGroupRepository(super_db_session)
        vg = repo.upsert_seen(p.id, loc.id, rate.id, "COMPACT", "Compact Car")
        assert vg.id is not None
        assert vg.external_code == "COMPACT"
        assert vg.external_name == "Compact Car"

    def test_upsert_seen_updates_existing(self, super_db_session):
        p = _provider(super_db_session, code="vg_update_test")
        loc = _location(super_db_session, p.id)
        rate = _rate(super_db_session, p.id)
        repo = ProviderVehicleGroupRepository(super_db_session)
        vg1 = repo.upsert_seen(p.id, loc.id, rate.id, "SUV", "SUV Old Name")
        first_seen = vg1.first_seen_at
        vg2 = repo.upsert_seen(p.id, loc.id, rate.id, "SUV", "SUV New Name")
        assert vg2.id == vg1.id
        assert vg2.external_name == "SUV New Name"
        assert vg2.first_seen_at == first_seen


class TestScrapeRunRepository:
    def test_create_and_mark_finished(self, super_db_session):
        p = _provider(super_db_session, code="run_test_prov")
        loc = _location(super_db_session, p.id)
        rate = _rate(super_db_session, p.id)
        repo = ScrapeRunRepository(super_db_session)
        run = repo.create(p.id, loc.id, rate.id)
        assert run.id is not None
        assert run.status == "running"
        repo.mark_finished(run.id, status="success", stats={"rows": 42})
        super_db_session.refresh(run)
        assert run.status == "success"
        assert run.finished_at is not None
        assert run.stats_jsonb == {"rows": 42}


class TestHomogeneousZoneRepository:
    def test_replace_zones_deactivates_old(self, super_db_session):
        p = _provider(super_db_session, code="zone_test_prov")
        loc = _location(super_db_session, p.id)
        rate = _rate(super_db_session, p.id)
        vg = _vehicle_group(super_db_session, p.id, loc.id, rate.id)

        old_zone = HomogeneousZone(
            provider_id=p.id,
            provider_location_id=loc.id,
            provider_rate_id=rate.id,
            provider_vehicle_group_id=vg.id,
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 1, 31),
            representative_date=datetime.date(2026, 1, 15),
            active=True,
        )
        super_db_session.add(old_zone)
        super_db_session.flush()

        new_zone = HomogeneousZone(
            provider_id=p.id,
            provider_location_id=loc.id,
            provider_rate_id=rate.id,
            provider_vehicle_group_id=vg.id,
            start_date=datetime.date(2026, 2, 1),
            end_date=datetime.date(2026, 2, 28),
            representative_date=datetime.date(2026, 2, 15),
            active=True,
        )
        repo = HomogeneousZoneRepository(super_db_session)
        repo.replace_zones_for_tuple(p.id, loc.id, rate.id, vg.id, [new_zone])

        super_db_session.refresh(old_zone)
        assert old_zone.active is False
        assert new_zone.id is not None
        assert new_zone.active is True


class TestTenantIsolation:
    """Verify that the RLS policy prevents cross-tenant data leakage.

    This test must commit via super_db_session so the inserted rows are visible
    to db_session (which uses a separate connection). Cleanup runs in
    finally to ensure the DB stays clean even if assertions fail.
    """

    def test_app_user_sees_only_own_tenant(self, super_db_session, db_session):
        t1_id: uuid.UUID | None = None
        t2_id: uuid.UUID | None = None
        try:
            t1 = _tenant(super_db_session)
            t2 = _tenant(super_db_session)
            u1 = User(tenant_id=t1.id, email="a@example.com", role="owner")
            u2 = User(tenant_id=t2.id, email="b@example.com", role="owner")
            super_db_session.add_all([u1, u2])
            super_db_session.commit()
            t1_id = t1.id
            t2_id = t2.id

            # Scope the app session to t1 and verify only t1's user is visible.
            # set_config with is_local=true is transaction-scoped; begin() ensures
            # we are inside a transaction when set_config runs.
            # Read all attributes inside the with-block: after the transaction
            # ends, app.tenant_id resets to '' and any lazy-load would fail the
            # RLS uuid cast.
            with db_session.begin():
                db_session.execute(
                    text("SELECT set_config('app.tenant_id', :tid, true)"),
                    {"tid": str(t1_id)},
                )
                emails = {
                    u.email
                    for u in db_session.scalars(select(User)).all()
                }

            assert "a@example.com" in emails
            assert "b@example.com" not in emails

        finally:
            if t1_id or t2_id:
                ids = [i for i in (t1_id, t2_id) if i is not None]
                super_db_session.execute(
                    delete(User).where(User.tenant_id.in_(ids))
                )
                super_db_session.execute(
                    delete(Tenant).where(Tenant.id.in_(ids))
                )
                super_db_session.commit()
