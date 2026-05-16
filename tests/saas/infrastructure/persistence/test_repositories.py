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
    CanonicalVehicleType,
    HomogeneousZone,
    Provider,
    ProviderLocation,
    ProviderRate,
    ProviderVehicleCategory,
    ScrapeRun,
)
from src.saas.infrastructure.persistence.models.tenant import Tenant, User
from src.saas.infrastructure.persistence.repositories import (
    HomogeneousZoneRepository,
    PriceObservationRepository,
    ProviderLocationRepository,
    ProviderRateRepository,
    ProviderRepository,
    ProviderVehicleCategoryRepository,
    ScrapeRunRepository,
)
from tests.saas.application._fakes import StubClassificationService


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
) -> ProviderVehicleCategory:
    vg = ProviderVehicleCategory(
        provider_id=provider_id,
        provider_location_id=provider_location_id,
        provider_rate_id=provider_rate_id,
        external_code=external_code,
        external_name="Economy",
        example_models="Fiat Panda, Kia Picanto",
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


class TestProviderVehicleCategoryRepository:
    def test_get_by_external_code_found(self, super_db_session):
        p = _provider(super_db_session, code="pvc_get_found")
        loc = _location(super_db_session, p.id)
        rate = _rate(super_db_session, p.id)
        _vehicle_group(super_db_session, p.id, loc.id, rate.id, external_code="ECMR")
        repo = ProviderVehicleCategoryRepository(super_db_session)
        result = repo.get_by_external_code(p.id, loc.id, rate.id, "ECMR")
        assert result is not None
        assert result.external_code == "ECMR"

    def test_get_by_external_code_not_found(self, super_db_session):
        p = _provider(super_db_session, code="pvc_get_missing")
        loc = _location(super_db_session, p.id)
        rate = _rate(super_db_session, p.id)
        repo = ProviderVehicleCategoryRepository(super_db_session)
        assert repo.get_by_external_code(p.id, loc.id, rate.id, "NO_SUCH") is None

    def test_list_active_for_tuple_returns_only_active(self, super_db_session):
        p = _provider(super_db_session, code="pvc_list_active")
        loc = _location(super_db_session, p.id)
        rate = _rate(super_db_session, p.id)
        active = ProviderVehicleCategory(
            provider_id=p.id, provider_location_id=loc.id, provider_rate_id=rate.id,
            external_code="ACTIVE", external_name="Active Car", example_models="", active=True,
        )
        inactive = ProviderVehicleCategory(
            provider_id=p.id, provider_location_id=loc.id, provider_rate_id=rate.id,
            external_code="INACTIVE", external_name="Inactive Car", example_models="", active=False,
        )
        super_db_session.add_all([active, inactive])
        super_db_session.flush()
        repo = ProviderVehicleCategoryRepository(super_db_session)
        results = repo.list_active_for_tuple(p.id, loc.id, rate.id)
        codes = {r.external_code for r in results}
        assert "ACTIVE" in codes
        assert "INACTIVE" not in codes

    def test_upsert_seen_updates_existing_row(self, super_db_session):
        p = _provider(super_db_session, code="pvc_upsert_update")
        loc = _location(super_db_session, p.id)
        rate = _rate(super_db_session, p.id)
        existing = ProviderVehicleCategory(
            provider_id=p.id, provider_location_id=loc.id, provider_rate_id=rate.id,
            external_code="SUV", external_name="SUV Old Name",
            example_models="Toyota RAV4", active=True,
        )
        super_db_session.add(existing)
        super_db_session.flush()
        first_seen = existing.first_seen_at

        stub = StubClassificationService({}, taxonomy_version=1)
        repo = ProviderVehicleCategoryRepository(super_db_session)
        result = repo.upsert_seen(
            provider_id=p.id, provider_location_id=loc.id, provider_rate_id=rate.id,
            external_code="SUV", external_name="SUV New Name",
            example_models="Toyota RAV4, Honda CR-V",
            seats=None, luggage=None, transmission=None, fuel_type=None,
            classification_service=stub, taxonomy_version=1,
        )
        assert result.id == existing.id
        assert result.external_name == "SUV New Name"
        assert result.example_models == "Toyota RAV4, Honda CR-V"
        assert result.first_seen_at == first_seen

    def test_upsert_seen_updates_group_attributes_when_changed(self, super_db_session):
        p = _provider(super_db_session, code="pvc_attrs_update")
        loc = _location(super_db_session, p.id)
        rate = _rate(super_db_session, p.id)
        existing = ProviderVehicleCategory(
            provider_id=p.id, provider_location_id=loc.id, provider_rate_id=rate.id,
            external_code="FCAR", external_name="Full Size",
            example_models="VW Passat", seats=5, luggage=3,
            transmission="manual", active=True,
        )
        super_db_session.add(existing)
        super_db_session.flush()

        stub = StubClassificationService({}, taxonomy_version=1)
        repo = ProviderVehicleCategoryRepository(super_db_session)
        result = repo.upsert_seen(
            provider_id=p.id, provider_location_id=loc.id, provider_rate_id=rate.id,
            external_code="FCAR", external_name="Full Size",
            example_models="VW Passat, Skoda Octavia",
            seats=5, luggage=4, transmission="automatic", fuel_type=None,
            classification_service=stub, taxonomy_version=1,
        )
        assert result.id == existing.id
        assert result.example_models == "VW Passat, Skoda Octavia"
        assert result.luggage == 4
        assert result.transmission == "automatic"

    # ------------------------------------------------------------------
    # Classification-aware upsert_seen tests
    # ------------------------------------------------------------------

    def test_upsert_seen_classifies_new_pvc(self, super_db_session):
        p = _provider(super_db_session, code="pvc_cls_new")
        loc = _location(super_db_session, p.id)
        rate = _rate(super_db_session, p.id)
        ct = CanonicalVehicleType(
            code="UTEST_ECO_MAN", name="Economy Manual (test)",
            description="Small manual car", taxonomy_version=1, active=True,
        )
        super_db_session.add(ct)
        super_db_session.flush()

        stub = StubClassificationService(
            {"ECMR": "UTEST_ECO_MAN"}, taxonomy_version=1, default_confidence=0.95
        )
        repo = ProviderVehicleCategoryRepository(super_db_session)
        pvc = repo.upsert_seen(
            provider_id=p.id, provider_location_id=loc.id, provider_rate_id=rate.id,
            external_code="ECMR", external_name="Economy",
            example_models="Fiat Panda", seats=5, luggage=2,
            transmission="manual", fuel_type=None,
            classification_service=stub, taxonomy_version=1,
        )

        assert pvc.canonical_type_id == ct.id
        assert pvc.classification_confidence == pytest.approx(0.95)
        assert pvc.classification_taxonomy_version == 1
        assert pvc.pending_review is False
        assert stub.call_count == 1

    def test_upsert_seen_skips_llm_when_taxonomy_version_matches(self, super_db_session):
        p = _provider(super_db_session, code="pvc_cls_cache")
        loc = _location(super_db_session, p.id)
        rate = _rate(super_db_session, p.id)
        ct = CanonicalVehicleType(
            code="UTEST_CMPCT_AUT", name="Compact Auto (test)",
            description="Compact automatic car", taxonomy_version=3, active=True,
        )
        super_db_session.add(ct)
        super_db_session.flush()

        existing = ProviderVehicleCategory(
            provider_id=p.id, provider_location_id=loc.id, provider_rate_id=rate.id,
            external_code="CCAR", external_name="Compact",
            example_models="Ford Focus", active=True,
            canonical_type_id=ct.id,
            classification_confidence=0.91,
            classification_taxonomy_version=3,
            pending_review=False,
        )
        super_db_session.add(existing)
        super_db_session.flush()

        stub = StubClassificationService({"CCAR": "UTEST_CMPCT_AUT"}, taxonomy_version=3)
        repo = ProviderVehicleCategoryRepository(super_db_session)
        result = repo.upsert_seen(
            provider_id=p.id, provider_location_id=loc.id, provider_rate_id=rate.id,
            external_code="CCAR", external_name="Compact",
            example_models="Ford Focus, VW Golf", seats=5, luggage=3,
            transmission="automatic", fuel_type=None,
            classification_service=stub, taxonomy_version=3,
        )

        assert stub.call_count == 0, "LLM must not be called when version matches"
        assert result.canonical_type_id == ct.id
        assert result.example_models == "Ford Focus, VW Golf"
        assert result.last_seen_at is not None

    def test_upsert_seen_reclassifies_when_taxonomy_version_changes(self, super_db_session):
        p = _provider(super_db_session, code="pvc_cls_reclassify")
        loc = _location(super_db_session, p.id)
        rate = _rate(super_db_session, p.id)
        old_ct = CanonicalVehicleType(
            code="OLD_CATEGORY", name="Old Category",
            description="Old", taxonomy_version=3, active=False,
        )
        new_ct = CanonicalVehicleType(
            code="NEW_CATEGORY", name="New Category",
            description="New", taxonomy_version=4, active=True,
        )
        super_db_session.add_all([old_ct, new_ct])
        super_db_session.flush()

        existing = ProviderVehicleCategory(
            provider_id=p.id, provider_location_id=loc.id, provider_rate_id=rate.id,
            external_code="CCAR", external_name="Compact",
            example_models="Ford Focus", active=True,
            canonical_type_id=old_ct.id,
            classification_taxonomy_version=3,
            pending_review=False,
        )
        super_db_session.add(existing)
        super_db_session.flush()

        stub = StubClassificationService({"CCAR": "NEW_CATEGORY"}, taxonomy_version=4)
        repo = ProviderVehicleCategoryRepository(super_db_session)
        result = repo.upsert_seen(
            provider_id=p.id, provider_location_id=loc.id, provider_rate_id=rate.id,
            external_code="CCAR", external_name="Compact",
            example_models="Ford Focus, VW Golf", seats=5, luggage=3,
            transmission="automatic", fuel_type=None,
            classification_service=stub, taxonomy_version=4,
        )

        assert stub.call_count == 1
        assert result.canonical_type_id == new_ct.id
        assert result.classification_taxonomy_version == 4
        assert result.pending_review is False

    def test_upsert_seen_keeps_cached_classification_on_pending_review_for_existing_pvc(
        self, super_db_session
    ):
        p = _provider(super_db_session, code="pvc_cls_cached_fallback")
        loc = _location(super_db_session, p.id)
        rate = _rate(super_db_session, p.id)
        ct = CanonicalVehicleType(
            code="UTEST_ECO_FALL", name="Economy Fallback (test)",
            description="Small manual car", taxonomy_version=3, active=True,
        )
        super_db_session.add(ct)
        super_db_session.flush()

        existing = ProviderVehicleCategory(
            provider_id=p.id, provider_location_id=loc.id, provider_rate_id=rate.id,
            external_code="ECMR", external_name="Economy",
            example_models="Fiat Panda", active=True,
            canonical_type_id=ct.id,
            classification_confidence=0.91,
            classification_taxonomy_version=3,
            pending_review=False,
        )
        super_db_session.add(existing)
        super_db_session.flush()
        original_canonical_id = ct.id

        # Stub for v4 returns pending_review (low confidence)
        stub = StubClassificationService({}, taxonomy_version=4)  # empty map → pending
        repo = ProviderVehicleCategoryRepository(super_db_session)
        result = repo.upsert_seen(
            provider_id=p.id, provider_location_id=loc.id, provider_rate_id=rate.id,
            external_code="ECMR", external_name="Economy",
            example_models="Fiat Panda", seats=5, luggage=2,
            transmission="manual", fuel_type=None,
            classification_service=stub, taxonomy_version=4,
        )

        assert result.canonical_type_id == original_canonical_id, \
            "Previous canonical_type_id must be preserved on pending_review"
        assert result.pending_review is True
        assert result.classification_confidence == pytest.approx(0.0)
        # taxonomy_version NOT updated — cached fallback keeps the old version
        assert result.classification_taxonomy_version == 3

    def test_upsert_seen_marks_pending_review_for_new_pvc_when_llm_uncertain(
        self, super_db_session
    ):
        p = _provider(super_db_session, code="pvc_cls_new_pending")
        loc = _location(super_db_session, p.id)
        rate = _rate(super_db_session, p.id)

        stub = StubClassificationService({}, taxonomy_version=1)  # always pending
        repo = ProviderVehicleCategoryRepository(super_db_session)
        pvc = repo.upsert_seen(
            provider_id=p.id, provider_location_id=loc.id, provider_rate_id=rate.id,
            external_code="XCAR", external_name="Unknown Group",
            example_models="Mystery Car", seats=None, luggage=None,
            transmission=None, fuel_type=None,
            classification_service=stub, taxonomy_version=1,
        )

        assert pvc.canonical_type_id is None
        assert pvc.pending_review is True
        assert pvc.classification_taxonomy_version == 1

    def test_upsert_seen_marks_pending_review_when_canonical_not_found_in_db(
        self, super_db_session
    ):
        p = _provider(super_db_session, code="pvc_cls_ghost")
        loc = _location(super_db_session, p.id)
        rate = _rate(super_db_session, p.id)

        # Stub returns 'GHOST_CATEGORY' but that code is not seeded in DB
        stub = StubClassificationService({"ECMR": "GHOST_CATEGORY"}, taxonomy_version=1)
        repo = ProviderVehicleCategoryRepository(super_db_session)
        pvc = repo.upsert_seen(
            provider_id=p.id, provider_location_id=loc.id, provider_rate_id=rate.id,
            external_code="ECMR", external_name="Economy",
            example_models="Fiat Panda", seats=5, luggage=2,
            transmission="manual", fuel_type=None,
            classification_service=stub, taxonomy_version=1,
        )

        assert pvc.canonical_type_id is None
        assert pvc.pending_review is True

    def test_upsert_seen_uses_attribute_hash_when_no_external_code(self, super_db_session):
        p = _provider(super_db_session, code="pvc_cls_no_code")
        loc = _location(super_db_session, p.id)
        rate = _rate(super_db_session, p.id)

        stub = StubClassificationService({}, taxonomy_version=1)
        repo = ProviderVehicleCategoryRepository(super_db_session)

        # First call: creates new PVC (no external_code)
        pvc1 = repo.upsert_seen(
            provider_id=p.id, provider_location_id=loc.id, provider_rate_id=rate.id,
            external_code=None, external_name=None,
            example_models="Fiat Panda", seats=5, luggage=2,
            transmission="manual", fuel_type="gasoline",
            classification_service=stub, taxonomy_version=1,
        )
        assert pvc1.id is not None
        assert stub.call_count == 1

        # Second call with same attributes: must find the existing row, not create a new one
        pvc2 = repo.upsert_seen(
            provider_id=p.id, provider_location_id=loc.id, provider_rate_id=rate.id,
            external_code=None, external_name=None,
            example_models="Fiat Panda", seats=5, luggage=2,
            transmission="manual", fuel_type="gasoline",
            classification_service=stub, taxonomy_version=1,  # same version → cache hit
            )
        assert pvc2.id == pvc1.id, "Second call must return the same PVC, not create a new row"
        # Version matches after first call → LLM not called again
        assert stub.call_count == 1, "classify() must not be called a second time when version matches"

        total = super_db_session.scalar(
            select(ProviderVehicleCategory).where(
                ProviderVehicleCategory.provider_id == p.id,
                ProviderVehicleCategory.external_code.is_(None),
            )
        )
        assert total is not None

    def test_upsert_seen_persists_none_external_name_as_null(self, super_db_session):
        p = _provider(super_db_session, code="pvc_null_extname")
        loc = _location(super_db_session, p.id)
        rate = _rate(super_db_session, p.id)

        canonical = CanonicalVehicleType(
            code="UTEST_NULL_NAME", name="Test Null Name",
            description="Test type for null external_name", taxonomy_version=1, active=True,
        )
        super_db_session.add(canonical)
        super_db_session.flush()

        stub = StubClassificationService({"EXT_NULL": "UTEST_NULL_NAME"}, taxonomy_version=1)
        repo = ProviderVehicleCategoryRepository(super_db_session)

        pvc = repo.upsert_seen(
            provider_id=p.id, provider_location_id=loc.id, provider_rate_id=rate.id,
            external_code="EXT_NULL", external_name=None,
            example_models="Ford Ka", seats=5, luggage=1,
            transmission="manual", fuel_type="gasoline",
            classification_service=stub, taxonomy_version=1,
        )

        super_db_session.refresh(pvc)
        assert pvc.external_name is None, "external_name=None must persist as SQL NULL, not empty string"


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
            provider_vehicle_category_id=vg.id,
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
            provider_vehicle_category_id=vg.id,
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
