"""End-to-end integration test for intra-provider aggregation.

Scenario: A single provider has two vehicle groups (EA and GA) that the
classifier maps to the *same* canonical type.  Before the revert this
scenario triggered a UniqueConstraint violation on uq_provider_vehicle_
categories_canonical; now it must produce two distinct PVC rows, and
PriceQueryService must aggregate them at query time (MIN policy).
"""
from __future__ import annotations

import datetime
import uuid
from decimal import Decimal
from datetime import date

import pytest
from sqlalchemy import delete

from src.saas.application.classification.dtos import ClassificationResult
from src.saas.application.price_query.service import PriceQueryService
from src.saas.infrastructure.persistence.models.catalog import (
    CanonicalVehicleType,
    HomogeneousZone,
    PriceObservation,
    PriceObservationHeartbeat,
    Provider,
    ProviderLocation,
    ProviderRate,
    ProviderVehicleCategory,
    ScrapeRun,
)
from src.saas.infrastructure.persistence.models.tenant import (
    Tenant,
    TenantSubscription,
    TenantVehicleGroup,
    TenantVehicleGroupMapping,
    User,
)
from src.saas.infrastructure.persistence.repositories import (
    ProviderVehicleCategoryRepository,
)


def _cleanup(session, provider_ids, tenant_ids=None, canonical_type_ids=None):
    session.rollback()
    if tenant_ids:
        for tid in tenant_ids:
            session.execute(
                delete(TenantVehicleGroupMapping).where(
                    TenantVehicleGroupMapping.tenant_id == tid
                )
            )
            session.execute(
                delete(TenantSubscription).where(TenantSubscription.tenant_id == tid)
            )
            session.execute(
                delete(TenantVehicleGroup).where(TenantVehicleGroup.tenant_id == tid)
            )
            session.execute(delete(User).where(User.tenant_id == tid))
            session.execute(delete(Tenant).where(Tenant.id == tid))
    for pid in provider_ids:
        session.execute(
            delete(PriceObservation).where(PriceObservation.provider_id == pid)
        )
        session.execute(
            delete(PriceObservationHeartbeat).where(
                PriceObservationHeartbeat.provider_id == pid
            )
        )
        session.execute(
            delete(HomogeneousZone).where(HomogeneousZone.provider_id == pid)
        )
        session.execute(delete(ScrapeRun).where(ScrapeRun.provider_id == pid))
        session.execute(
            delete(ProviderVehicleCategory).where(
                ProviderVehicleCategory.provider_id == pid
            )
        )
        session.execute(delete(ProviderRate).where(ProviderRate.provider_id == pid))
        session.execute(
            delete(ProviderLocation).where(ProviderLocation.provider_id == pid)
        )
        session.execute(delete(Provider).where(Provider.id == pid))
    if canonical_type_ids:
        for ctid in canonical_type_ids:
            session.execute(
                delete(CanonicalVehicleType).where(CanonicalVehicleType.id == ctid)
            )
    session.commit()


def test_two_pvcs_same_canonical_coexist_and_query_returns_min(super_db_session):
    """Full pipeline: upsert_seen twice → same canonical → PriceQueryService returns min.

    This is the exact scenario that motivated the intra-provider aggregation
    revert: a provider has two price tiers (EA cheaper, GA more expensive) that
    both classify as the same canonical type.  The old constraint would raise
    UniqueConstraint on the second upsert_seen call; the new design allows both
    rows and aggregates at query time.
    """
    p_id: int | None = None
    ct_id: int | None = None
    t_id: uuid.UUID | None = None

    try:
        # ── Catalog ────────────────────────────────────────────────────────
        p = Provider(
            code="integ_agg_p01",
            display_name="Integ Agg Provider",
            scraper_key="integ_agg_p01",
            default_currency="EUR",
            status="active",
        )
        super_db_session.add(p)
        super_db_session.flush()
        p_id = p.id

        loc = ProviderLocation(
            provider_id=p.id,
            location_code="AGG1",
            location_name="Aggregation Test Location",
            active=True,
        )
        super_db_session.add(loc)
        super_db_session.flush()

        rate = ProviderRate(
            provider_id=p.id,
            rate_code="integ_rate",
            rate_name="Integ Rate",
            active=True,
        )
        super_db_session.add(rate)
        super_db_session.flush()

        ct = CanonicalVehicleType(
            code="integ_intermediate_auto",
            name="Intermediate Automatic",
            description="Mid-size automatic",
            taxonomy_version=1,
            active=True,
        )
        super_db_session.add(ct)
        super_db_session.flush()
        ct_id = ct.id

        # ── Classify and upsert two PVCs that share the same canonical type ─
        classification = ClassificationResult(
            canonical_type_code="integ_intermediate_auto",
            confidence=0.95,
            taxonomy_version=1,
            pending_review=False,
        )
        repo = ProviderVehicleCategoryRepository(super_db_session)

        pvc_ea = repo.upsert_seen(
            provider_id=p.id,
            provider_location_id=loc.id,
            provider_rate_id=rate.id,
            external_code="EA",
            external_name="Economy Automatic EA",
            example_models="VW Golf, Toyota Corolla",
            seats=5,
            luggage=2,
            transmission="automatic",
            fuel_type="petrol",
            classification=classification,
        )

        pvc_ga = repo.upsert_seen(
            provider_id=p.id,
            provider_location_id=loc.id,
            provider_rate_id=rate.id,
            external_code="GA",
            external_name="Group Automatic GA",
            example_models="BMW 1 Series, Audi A3",
            seats=5,
            luggage=2,
            transmission="automatic",
            fuel_type="petrol",
            classification=classification,
        )

        # Both rows must exist; both point to the same canonical type
        assert pvc_ea.id != pvc_ga.id, "Two distinct PVC rows must have been created"
        assert pvc_ea.canonical_type_id == ct.id
        assert pvc_ga.canonical_type_id == ct.id
        assert pvc_ea.canonical_type_id == pvc_ga.canonical_type_id

        # ── Zones and observations ─────────────────────────────────────────
        run = ScrapeRun(
            provider_id=p.id,
            provider_location_id=loc.id,
            provider_rate_id=rate.id,
            status="success",
        )
        super_db_session.add(run)
        super_db_session.flush()

        rep = date(2026, 6, 15)

        for pvc in (pvc_ea, pvc_ga):
            super_db_session.add(HomogeneousZone(
                provider_id=p.id,
                provider_location_id=loc.id,
                provider_rate_id=rate.id,
                provider_vehicle_category_id=pvc.id,
                start_date=date(2026, 6, 1),
                end_date=date(2026, 8, 31),
                representative_date=rep,
                active=True,
            ))

        super_db_session.flush()

        # EA is cheaper (€57/day), GA is more expensive (€69/day)
        for pvc, price in ((pvc_ea, Decimal("57.00")), (pvc_ga, Decimal("69.00"))):
            super_db_session.add(PriceObservation(
                provider_id=p.id,
                provider_location_id=loc.id,
                provider_rate_id=rate.id,
                provider_vehicle_category_id=pvc.id,
                scrape_run_id=run.id,
                pickup_date=rep,
                duration_days=7,
                price_per_day=price,
                total_price=price * 7,
                currency="EUR",
                observed_at=datetime.datetime(2026, 5, 1, 10, 0, 0),
            ))

        super_db_session.flush()

        # ── Tenant, subscription, mapping ─────────────────────────────────
        tenant = Tenant(name="Integ Agg Tenant", currency="EUR")
        super_db_session.add(tenant)
        super_db_session.flush()
        t_id = tenant.id

        sub = TenantSubscription(
            tenant_id=tenant.id,
            provider_id=p.id,
            provider_location_id=loc.id,
            provider_rate_id=rate.id,
            status="active",
        )
        super_db_session.add(sub)

        cvg = TenantVehicleGroup(
            tenant_id=tenant.id,
            code="intermediate",
            name="Intermediate",
            display_order=1,
        )
        super_db_session.add(cvg)
        super_db_session.flush()

        super_db_session.add(TenantVehicleGroupMapping(
            tenant_id=tenant.id,
            tenant_vehicle_group_id=cvg.id,
            canonical_type_id=ct.id,
        ))
        super_db_session.flush()

        # ── Query ──────────────────────────────────────────────────────────
        service = PriceQueryService(super_db_session)
        result = service.get_provider_tariff(
            tenant.id,
            "integ_agg_p01",
            "AGG1",
            "integ_rate",
            (date(2026, 6, 1), date(2026, 8, 31)),
            ["intermediate"],
            [7],
        )

        # One row (one zone for the client group), price = min(57, 69) = 57
        assert len(result.rows) == 1, (
            f"Expected 1 row but got {len(result.rows)} — "
            "aggregation across same-canonical PVCs should produce 1 logical group"
        )
        row = result.rows[0]
        assert row.prices_by_duration[7] == Decimal("57.00"), (
            f"Expected min(57, 69)=57 but got {row.prices_by_duration[7]} — "
            "PriceQueryService must aggregate at query time using MIN"
        )

    finally:
        _cleanup(
            super_db_session,
            provider_ids=[p_id] if p_id is not None else [],
            tenant_ids=[t_id] if t_id is not None else [],
            canonical_type_ids=[ct_id] if ct_id is not None else [],
        )
