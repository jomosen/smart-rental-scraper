"""Tests for PriceQueryService and compute_intersected_grid.

Unit tests (TestComputeIntersectedGrid): pure function, no DB required.
Integration tests: require local Postgres with migrations applied.
Each integration test that commits data cleans up in a finally block.
"""
from __future__ import annotations

import datetime
import uuid
from decimal import Decimal
from typing import Optional

import pytest
from sqlalchemy import delete, text

from src.saas.application.price_query.dtos import FormatARow, FormatATable, ZoneRange
from src.saas.application.price_query.rejilla import compute_intersected_grid
from src.saas.application.price_query.service import PriceQueryService
from src.saas.infrastructure.persistence.models.catalog import (
    HomogeneousZone,
    PriceObservation,
    PriceObservationHeartbeat,
    Provider,
    ProviderLocation,
    ProviderRate,
    ProviderVehicleGroup,
    ScrapeRun,
)
from src.saas.infrastructure.persistence.models.tenant import (
    ClientVehicleGroup,
    Tenant,
    TenantSubscription,
    User,
    VehicleGroupMapping,
)

from datetime import date

# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

def _seed_provider(session, code: str = "pq_sc_a") -> Provider:
    p = Provider(
        code=code,
        display_name="PQ Test Provider",
        scraper_key=code,
        default_currency="EUR",
        status="active",
    )
    session.add(p)
    session.flush()
    return p


def _seed_location(session, provider_id: int, code: str = "PQ1") -> ProviderLocation:
    loc = ProviderLocation(
        provider_id=provider_id,
        location_code=code,
        location_name="PQ Location",
        active=True,
    )
    session.add(loc)
    session.flush()
    return loc


def _seed_rate(session, provider_id: int, code: str = "test_rate") -> ProviderRate:
    r = ProviderRate(
        provider_id=provider_id,
        rate_code=code,
        rate_name="Test Rate",
        active=True,
    )
    session.add(r)
    session.flush()
    return r


def _seed_pvg(
    session, pid: int, lid: int, rid: int, code: str = "ECMR"
) -> ProviderVehicleGroup:
    pvg = ProviderVehicleGroup(
        provider_id=pid,
        provider_location_id=lid,
        provider_rate_id=rid,
        external_code=code,
        external_name=code,
        active=True,
    )
    session.add(pvg)
    session.flush()
    return pvg


def _seed_scrape_run(session, pid: int, lid: int, rid: int) -> ScrapeRun:
    run = ScrapeRun(
        provider_id=pid,
        provider_location_id=lid,
        provider_rate_id=rid,
        status="success",
    )
    session.add(run)
    session.flush()
    return run


def _seed_zone(
    session,
    pid: int,
    lid: int,
    rid: int,
    pvg_id: int,
    start: date,
    end: date,
    rep: date,
    active: bool = True,
) -> HomogeneousZone:
    z = HomogeneousZone(
        provider_id=pid,
        provider_location_id=lid,
        provider_rate_id=rid,
        provider_vehicle_group_id=pvg_id,
        start_date=start,
        end_date=end,
        representative_date=rep,
        active=active,
    )
    session.add(z)
    session.flush()
    return z


def _seed_observation(
    session,
    pid: int,
    lid: int,
    rid: int,
    pvg_id: int,
    run_id: int,
    pickup_date: date,
    duration_days: int,
    price_per_day: Decimal,
) -> None:
    obs = PriceObservation(
        provider_id=pid,
        provider_location_id=lid,
        provider_rate_id=rid,
        provider_vehicle_group_id=pvg_id,
        scrape_run_id=run_id,
        pickup_date=pickup_date,
        duration_days=duration_days,
        price_per_day=price_per_day,
        total_price=price_per_day * duration_days,
        currency="EUR",
        observed_at=datetime.datetime(2026, 5, 1, 10, 0, 0),  # May partition exists
    )
    session.add(obs)
    session.flush()


def _seed_tenant(session, name: str = "PQ Tenant", currency: str = "EUR") -> Tenant:
    t = Tenant(name=name, currency=currency)
    session.add(t)
    session.flush()
    return t


def _seed_subscription(
    session,
    tenant_id: uuid.UUID,
    pid: int,
    lid: int,
    rid: int,
    status: str = "active",
) -> TenantSubscription:
    sub = TenantSubscription(
        tenant_id=tenant_id,
        provider_id=pid,
        provider_location_id=lid,
        provider_rate_id=rid,
        status=status,
    )
    session.add(sub)
    session.flush()
    return sub


def _seed_client_group(
    session,
    tenant_id: uuid.UUID,
    code: str = "compact",
    name: str = "Compact",
    display_order: int = 1,
) -> ClientVehicleGroup:
    cvg = ClientVehicleGroup(
        tenant_id=tenant_id,
        code=code,
        name=name,
        display_order=display_order,
    )
    session.add(cvg)
    session.flush()
    return cvg


def _seed_mapping(
    session,
    tenant_id: uuid.UUID,
    cvg_id: uuid.UUID,
    pvg_id: int,
) -> VehicleGroupMapping:
    m = VehicleGroupMapping(
        tenant_id=tenant_id,
        client_vehicle_group_id=cvg_id,
        provider_vehicle_group_id=pvg_id,
    )
    session.add(m)
    session.flush()
    return m


def _cleanup(
    session,
    provider_ids: list[int],
    tenant_ids: list[uuid.UUID] | None = None,
) -> None:
    """Delete test data in FK-safe order and commit."""
    session.rollback()
    if tenant_ids:
        for tid in tenant_ids:
            session.execute(
                delete(VehicleGroupMapping).where(VehicleGroupMapping.tenant_id == tid)
            )
            session.execute(
                delete(TenantSubscription).where(TenantSubscription.tenant_id == tid)
            )
            session.execute(
                delete(ClientVehicleGroup).where(ClientVehicleGroup.tenant_id == tid)
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
            delete(ProviderVehicleGroup).where(ProviderVehicleGroup.provider_id == pid)
        )
        session.execute(delete(ProviderRate).where(ProviderRate.provider_id == pid))
        session.execute(
            delete(ProviderLocation).where(ProviderLocation.provider_id == pid)
        )
        session.execute(delete(Provider).where(Provider.id == pid))
    session.commit()


# ---------------------------------------------------------------------------
# Unit tests — compute_intersected_grid (no DB required)
# ---------------------------------------------------------------------------


class TestComputeIntersectedGrid:
    def test_grid_with_single_provider_returns_its_zones(self):
        zones = {
            1: [
                ZoneRange(date(2026, 6, 1), date(2026, 7, 14)),
                ZoneRange(date(2026, 7, 15), date(2026, 8, 31)),
            ]
        }
        result = compute_intersected_grid(zones, (date(2026, 6, 1), date(2026, 8, 31)))
        assert result == [
            (date(2026, 6, 1), date(2026, 7, 14)),
            (date(2026, 7, 15), date(2026, 8, 31)),
        ]

    def test_grid_with_two_providers_unions_cut_points(self):
        zones = {
            1: [
                ZoneRange(date(2026, 5, 1), date(2026, 7, 14)),
                ZoneRange(date(2026, 7, 15), date(2026, 8, 31)),
            ],
            2: [
                ZoneRange(date(2026, 5, 1), date(2026, 7, 7)),
                ZoneRange(date(2026, 7, 8), date(2026, 8, 31)),
            ],
        }
        result = compute_intersected_grid(zones, (date(2026, 5, 1), date(2026, 8, 31)))
        assert result == [
            (date(2026, 5, 1), date(2026, 7, 7)),
            (date(2026, 7, 8), date(2026, 7, 14)),
            (date(2026, 7, 15), date(2026, 8, 31)),
        ]

    def test_grid_clips_zones_to_date_range(self):
        # Zone extends beyond the requested date_range on both sides
        zones = {
            1: [ZoneRange(date(2026, 5, 1), date(2026, 8, 31))]
        }
        result = compute_intersected_grid(zones, (date(2026, 6, 1), date(2026, 7, 31)))
        assert result == [(date(2026, 6, 1), date(2026, 7, 31))]

    def test_grid_with_no_zones_returns_empty(self):
        assert compute_intersected_grid({}, (date(2026, 6, 1), date(2026, 8, 31))) == []
        assert compute_intersected_grid({1: []}, (date(2026, 6, 1), date(2026, 8, 31))) == []

    def test_grid_handles_zones_starting_or_ending_at_range_boundary(self):
        # Zone exactly covers the range boundary
        D1 = date(2026, 6, 1)
        D2 = date(2026, 8, 31)
        zones = {
            1: [
                ZoneRange(D1, date(2026, 7, 15)),
                ZoneRange(date(2026, 7, 16), D2),
            ]
        }
        result = compute_intersected_grid(zones, (D1, D2))
        assert result[0][0] == D1
        assert result[-1][1] == D2
        assert result == [
            (date(2026, 6, 1), date(2026, 7, 15)),
            (date(2026, 7, 16), date(2026, 8, 31)),
        ]


# ---------------------------------------------------------------------------
# Integration tests — PriceQueryService
# ---------------------------------------------------------------------------


def test_get_provider_tariff_returns_one_row_per_zone_per_group(super_db_session):
    p = _seed_provider(super_db_session, "pq_t06")
    loc = _seed_location(super_db_session, p.id)
    rate = _seed_rate(super_db_session, p.id)
    pvg = _seed_pvg(super_db_session, p.id, loc.id, rate.id, "ECMR")
    run = _seed_scrape_run(super_db_session, p.id, loc.id, rate.id)
    _seed_zone(super_db_session, p.id, loc.id, rate.id, pvg.id,
               date(2026, 6, 1), date(2026, 7, 14), date(2026, 6, 15))
    _seed_zone(super_db_session, p.id, loc.id, rate.id, pvg.id,
               date(2026, 7, 15), date(2026, 8, 31), date(2026, 7, 31))
    _seed_observation(super_db_session, p.id, loc.id, rate.id, pvg.id, run.id,
                      date(2026, 6, 15), 7, Decimal("45.00"))
    _seed_observation(super_db_session, p.id, loc.id, rate.id, pvg.id, run.id,
                      date(2026, 7, 31), 7, Decimal("65.00"))

    t = _seed_tenant(super_db_session)
    cvg = _seed_client_group(super_db_session, t.id)
    _seed_subscription(super_db_session, t.id, p.id, loc.id, rate.id)
    _seed_mapping(super_db_session, t.id, cvg.id, pvg.id)

    service = PriceQueryService(super_db_session)
    result = service.get_provider_tariff(
        t.id, "pq_t06", "PQ1", "test_rate",
        (date(2026, 6, 1), date(2026, 8, 31)),
        ["compact"],
        [7],
    )

    assert len(result.rows) == 2
    row1 = next(r for r in result.rows if r.period_start == date(2026, 6, 1))
    row2 = next(r for r in result.rows if r.period_start == date(2026, 7, 15))
    assert row1.period_end == date(2026, 7, 14)
    assert row1.prices_by_duration[7] == Decimal("45.00")
    assert row1.coverage is None
    assert row2.prices_by_duration[7] == Decimal("65.00")
    assert row2.coverage is None
    assert result.metadata["currency"] == "EUR"


def test_get_provider_tariff_returns_empty_when_no_active_subscription(super_db_session):
    p = _seed_provider(super_db_session, "pq_t07")
    loc = _seed_location(super_db_session, p.id)
    rate = _seed_rate(super_db_session, p.id)
    pvg = _seed_pvg(super_db_session, p.id, loc.id, rate.id, "ECMR")
    _seed_zone(super_db_session, p.id, loc.id, rate.id, pvg.id,
               date(2026, 6, 1), date(2026, 8, 31), date(2026, 6, 15))

    t = _seed_tenant(super_db_session)
    cvg = _seed_client_group(super_db_session, t.id)
    # subscription is pending_mapping, not active
    _seed_subscription(super_db_session, t.id, p.id, loc.id, rate.id,
                       status="pending_mapping")
    _seed_mapping(super_db_session, t.id, cvg.id, pvg.id)

    service = PriceQueryService(super_db_session)
    result = service.get_provider_tariff(
        t.id, "pq_t07", "PQ1", "test_rate",
        (date(2026, 6, 1), date(2026, 8, 31)),
        ["compact"],
        [7],
    )

    assert result.rows == []
    assert "warning" in result.metadata


def test_get_provider_tariff_applies_min_policy_for_n_to_m_mappings(super_db_session):
    p = _seed_provider(super_db_session, "pq_t08")
    loc = _seed_location(super_db_session, p.id)
    rate = _seed_rate(super_db_session, p.id)
    pvg_ecmr = _seed_pvg(super_db_session, p.id, loc.id, rate.id, "ECMR")
    pvg_ccar = _seed_pvg(super_db_session, p.id, loc.id, rate.id, "CCAR")
    run = _seed_scrape_run(super_db_session, p.id, loc.id, rate.id)
    rep = date(2026, 6, 15)
    _seed_zone(super_db_session, p.id, loc.id, rate.id, pvg_ecmr.id,
               date(2026, 6, 1), date(2026, 8, 31), rep)
    _seed_zone(super_db_session, p.id, loc.id, rate.id, pvg_ccar.id,
               date(2026, 6, 1), date(2026, 8, 31), rep)
    _seed_observation(super_db_session, p.id, loc.id, rate.id, pvg_ecmr.id, run.id,
                      rep, 7, Decimal("45.00"))
    _seed_observation(super_db_session, p.id, loc.id, rate.id, pvg_ccar.id, run.id,
                      rep, 7, Decimal("38.00"))

    t = _seed_tenant(super_db_session)
    cvg = _seed_client_group(super_db_session, t.id)
    _seed_subscription(super_db_session, t.id, p.id, loc.id, rate.id)
    # Both PVGs map to the same client group (N:M)
    _seed_mapping(super_db_session, t.id, cvg.id, pvg_ecmr.id)
    _seed_mapping(super_db_session, t.id, cvg.id, pvg_ccar.id)

    service = PriceQueryService(super_db_session)
    result = service.get_provider_tariff(
        t.id, "pq_t08", "PQ1", "test_rate",
        (date(2026, 6, 1), date(2026, 8, 31)),
        ["compact"],
        [7],
    )

    assert len(result.rows) == 1
    # N:M policy: min(45, 38) = 38
    assert result.rows[0].prices_by_duration[7] == Decimal("38.00")


def test_get_provider_tariff_returns_null_when_no_observation_for_duration(super_db_session):
    p = _seed_provider(super_db_session, "pq_t09")
    loc = _seed_location(super_db_session, p.id)
    rate = _seed_rate(super_db_session, p.id)
    pvg = _seed_pvg(super_db_session, p.id, loc.id, rate.id, "ECMR")
    run = _seed_scrape_run(super_db_session, p.id, loc.id, rate.id)
    rep = date(2026, 6, 15)
    _seed_zone(super_db_session, p.id, loc.id, rate.id, pvg.id,
               date(2026, 6, 1), date(2026, 8, 31), rep)
    # Only duration 1 has an observation — duration 7 is absent
    _seed_observation(super_db_session, p.id, loc.id, rate.id, pvg.id, run.id,
                      rep, 1, Decimal("55.00"))

    t = _seed_tenant(super_db_session)
    cvg = _seed_client_group(super_db_session, t.id)
    _seed_subscription(super_db_session, t.id, p.id, loc.id, rate.id)
    _seed_mapping(super_db_session, t.id, cvg.id, pvg.id)

    service = PriceQueryService(super_db_session)
    result = service.get_provider_tariff(
        t.id, "pq_t09", "PQ1", "test_rate",
        (date(2026, 6, 1), date(2026, 8, 31)),
        ["compact"],
        [1, 7],
    )

    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.prices_by_duration[1] == Decimal("55.00")
    assert row.prices_by_duration[7] is None


def test_get_market_average_uses_intersected_grid_across_providers(super_db_session):
    # Provider A: one zone covering the full period
    p_a = _seed_provider(super_db_session, "pq_t10a")
    loc_a = _seed_location(super_db_session, p_a.id)
    rate_a = _seed_rate(super_db_session, p_a.id)
    pvg_a = _seed_pvg(super_db_session, p_a.id, loc_a.id, rate_a.id, "ECMR")
    run_a = _seed_scrape_run(super_db_session, p_a.id, loc_a.id, rate_a.id)
    rep_a = date(2026, 6, 15)
    _seed_zone(super_db_session, p_a.id, loc_a.id, rate_a.id, pvg_a.id,
               date(2026, 6, 1), date(2026, 8, 31), rep_a)
    _seed_observation(super_db_session, p_a.id, loc_a.id, rate_a.id, pvg_a.id, run_a.id,
                      rep_a, 7, Decimal("40.00"))

    # Provider B: two zones (creates an extra cut point)
    p_b = _seed_provider(super_db_session, "pq_t10b")
    loc_b = _seed_location(super_db_session, p_b.id)
    rate_b = _seed_rate(super_db_session, p_b.id)
    pvg_b = _seed_pvg(super_db_session, p_b.id, loc_b.id, rate_b.id, "ECMR")
    run_b = _seed_scrape_run(super_db_session, p_b.id, loc_b.id, rate_b.id)
    rep_b1 = date(2026, 6, 15)
    rep_b2 = date(2026, 7, 31)
    _seed_zone(super_db_session, p_b.id, loc_b.id, rate_b.id, pvg_b.id,
               date(2026, 6, 1), date(2026, 7, 14), rep_b1)
    _seed_zone(super_db_session, p_b.id, loc_b.id, rate_b.id, pvg_b.id,
               date(2026, 7, 15), date(2026, 8, 31), rep_b2)
    _seed_observation(super_db_session, p_b.id, loc_b.id, rate_b.id, pvg_b.id, run_b.id,
                      rep_b1, 7, Decimal("50.00"))
    _seed_observation(super_db_session, p_b.id, loc_b.id, rate_b.id, pvg_b.id, run_b.id,
                      rep_b2, 7, Decimal("60.00"))

    t = _seed_tenant(super_db_session)
    cvg = _seed_client_group(super_db_session, t.id)
    _seed_subscription(super_db_session, t.id, p_a.id, loc_a.id, rate_a.id)
    _seed_subscription(super_db_session, t.id, p_b.id, loc_b.id, rate_b.id)
    _seed_mapping(super_db_session, t.id, cvg.id, pvg_a.id)
    _seed_mapping(super_db_session, t.id, cvg.id, pvg_b.id)

    service = PriceQueryService(super_db_session)
    result = service.get_market_average_tariff(
        t.id,
        (date(2026, 6, 1), date(2026, 8, 31)),
        ["compact"],
        [7],
    )

    # Intersected grid: (Jun 1 - Jul 14), (Jul 15 - Aug 31)
    assert len(result.rows) == 2
    rows_by_start = {r.period_start: r for r in result.rows}

    row1 = rows_by_start[date(2026, 6, 1)]
    assert row1.period_end == date(2026, 7, 14)
    # A: 40, B: 50 → avg = 45
    assert row1.prices_by_duration[7] == Decimal("45.00")
    assert row1.coverage == 2

    row2 = rows_by_start[date(2026, 7, 15)]
    assert row2.period_end == date(2026, 8, 31)
    # A: 40 (zone Jun 1 – Aug 31, rep Jun 15), B: 60 (zone Jul 15 – Aug 31, rep Jul 31)
    assert row2.prices_by_duration[7] == Decimal("50.00")
    assert row2.coverage == 2


def test_get_market_average_reports_coverage_per_cell(super_db_session):
    # Provider A: has price for dur 7 only
    p_a = _seed_provider(super_db_session, "pq_t11a")
    loc_a = _seed_location(super_db_session, p_a.id)
    rate_a = _seed_rate(super_db_session, p_a.id)
    pvg_a = _seed_pvg(super_db_session, p_a.id, loc_a.id, rate_a.id, "ECMR")
    run_a = _seed_scrape_run(super_db_session, p_a.id, loc_a.id, rate_a.id)
    rep = date(2026, 6, 15)
    _seed_zone(super_db_session, p_a.id, loc_a.id, rate_a.id, pvg_a.id,
               date(2026, 6, 1), date(2026, 8, 31), rep)
    _seed_observation(super_db_session, p_a.id, loc_a.id, rate_a.id, pvg_a.id, run_a.id,
                      rep, 7, Decimal("45.00"))
    # dur 1 is intentionally absent for Provider A

    # Provider B: has prices for both dur 1 and dur 7
    p_b = _seed_provider(super_db_session, "pq_t11b")
    loc_b = _seed_location(super_db_session, p_b.id)
    rate_b = _seed_rate(super_db_session, p_b.id)
    pvg_b = _seed_pvg(super_db_session, p_b.id, loc_b.id, rate_b.id, "ECMR")
    run_b = _seed_scrape_run(super_db_session, p_b.id, loc_b.id, rate_b.id)
    _seed_zone(super_db_session, p_b.id, loc_b.id, rate_b.id, pvg_b.id,
               date(2026, 6, 1), date(2026, 8, 31), rep)
    _seed_observation(super_db_session, p_b.id, loc_b.id, rate_b.id, pvg_b.id, run_b.id,
                      rep, 7, Decimal("55.00"))
    _seed_observation(super_db_session, p_b.id, loc_b.id, rate_b.id, pvg_b.id, run_b.id,
                      rep, 1, Decimal("60.00"))

    t = _seed_tenant(super_db_session)
    cvg = _seed_client_group(super_db_session, t.id)
    _seed_subscription(super_db_session, t.id, p_a.id, loc_a.id, rate_a.id)
    _seed_subscription(super_db_session, t.id, p_b.id, loc_b.id, rate_b.id)
    _seed_mapping(super_db_session, t.id, cvg.id, pvg_a.id)
    _seed_mapping(super_db_session, t.id, cvg.id, pvg_b.id)

    service = PriceQueryService(super_db_session)
    result = service.get_market_average_tariff(
        t.id,
        (date(2026, 6, 1), date(2026, 8, 31)),
        ["compact"],
        [1, 7],
    )

    assert len(result.rows) == 1
    row = result.rows[0]
    # Both providers contribute at least one price → coverage = 2
    assert row.coverage == 2
    # dur 7: average(45, 55) = 50
    assert row.prices_by_duration[7] == Decimal("50.00")
    # dur 1: only Provider B contributes → 60 (no average, single value)
    assert row.prices_by_duration[1] == Decimal("60.00")


def test_get_market_average_excludes_inactive_subscriptions(super_db_session):
    rep = date(2026, 6, 15)

    # Provider A: active subscription
    p_a = _seed_provider(super_db_session, "pq_t12a")
    loc_a = _seed_location(super_db_session, p_a.id)
    rate_a = _seed_rate(super_db_session, p_a.id)
    pvg_a = _seed_pvg(super_db_session, p_a.id, loc_a.id, rate_a.id, "ECMR")
    run_a = _seed_scrape_run(super_db_session, p_a.id, loc_a.id, rate_a.id)
    _seed_zone(super_db_session, p_a.id, loc_a.id, rate_a.id, pvg_a.id,
               date(2026, 6, 1), date(2026, 8, 31), rep)
    _seed_observation(super_db_session, p_a.id, loc_a.id, rate_a.id, pvg_a.id, run_a.id,
                      rep, 7, Decimal("40.00"))

    # Provider B: paused subscription (should be excluded)
    p_b = _seed_provider(super_db_session, "pq_t12b")
    loc_b = _seed_location(super_db_session, p_b.id)
    rate_b = _seed_rate(super_db_session, p_b.id)
    pvg_b = _seed_pvg(super_db_session, p_b.id, loc_b.id, rate_b.id, "ECMR")
    run_b = _seed_scrape_run(super_db_session, p_b.id, loc_b.id, rate_b.id)
    _seed_zone(super_db_session, p_b.id, loc_b.id, rate_b.id, pvg_b.id,
               date(2026, 6, 1), date(2026, 8, 31), rep)
    _seed_observation(super_db_session, p_b.id, loc_b.id, rate_b.id, pvg_b.id, run_b.id,
                      rep, 7, Decimal("99.00"))

    t = _seed_tenant(super_db_session)
    cvg = _seed_client_group(super_db_session, t.id)
    _seed_subscription(super_db_session, t.id, p_a.id, loc_a.id, rate_a.id, status="active")
    _seed_subscription(super_db_session, t.id, p_b.id, loc_b.id, rate_b.id, status="paused")
    _seed_mapping(super_db_session, t.id, cvg.id, pvg_a.id)
    _seed_mapping(super_db_session, t.id, cvg.id, pvg_b.id)

    service = PriceQueryService(super_db_session)
    result = service.get_market_average_tariff(
        t.id,
        (date(2026, 6, 1), date(2026, 8, 31)),
        ["compact"],
        [7],
    )

    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.coverage == 1  # only Provider A counts
    assert row.prices_by_duration[7] == Decimal("40.00")  # only Provider A's price


def test_get_market_minimum_returns_min_across_providers(super_db_session):
    rep = date(2026, 6, 15)

    p_a = _seed_provider(super_db_session, "pq_t13a")
    loc_a = _seed_location(super_db_session, p_a.id)
    rate_a = _seed_rate(super_db_session, p_a.id)
    pvg_a = _seed_pvg(super_db_session, p_a.id, loc_a.id, rate_a.id, "ECMR")
    run_a = _seed_scrape_run(super_db_session, p_a.id, loc_a.id, rate_a.id)
    _seed_zone(super_db_session, p_a.id, loc_a.id, rate_a.id, pvg_a.id,
               date(2026, 6, 1), date(2026, 8, 31), rep)
    _seed_observation(super_db_session, p_a.id, loc_a.id, rate_a.id, pvg_a.id, run_a.id,
                      rep, 7, Decimal("45.00"))

    p_b = _seed_provider(super_db_session, "pq_t13b")
    loc_b = _seed_location(super_db_session, p_b.id)
    rate_b = _seed_rate(super_db_session, p_b.id)
    pvg_b = _seed_pvg(super_db_session, p_b.id, loc_b.id, rate_b.id, "ECMR")
    run_b = _seed_scrape_run(super_db_session, p_b.id, loc_b.id, rate_b.id)
    _seed_zone(super_db_session, p_b.id, loc_b.id, rate_b.id, pvg_b.id,
               date(2026, 6, 1), date(2026, 8, 31), rep)
    _seed_observation(super_db_session, p_b.id, loc_b.id, rate_b.id, pvg_b.id, run_b.id,
                      rep, 7, Decimal("55.00"))

    t = _seed_tenant(super_db_session)
    cvg = _seed_client_group(super_db_session, t.id)
    _seed_subscription(super_db_session, t.id, p_a.id, loc_a.id, rate_a.id)
    _seed_subscription(super_db_session, t.id, p_b.id, loc_b.id, rate_b.id)
    _seed_mapping(super_db_session, t.id, cvg.id, pvg_a.id)
    _seed_mapping(super_db_session, t.id, cvg.id, pvg_b.id)

    service = PriceQueryService(super_db_session)
    result = service.get_market_minimum_tariff(
        t.id,
        (date(2026, 6, 1), date(2026, 8, 31)),
        ["compact"],
        [7],
    )

    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.coverage == 2
    assert row.prices_by_duration[7] == Decimal("45.00")  # min(45, 55)


def test_get_market_minimum_handles_partial_coverage(super_db_session):
    # Provider A: zone only covers tramo 1 (Jun 1 - Jul 14)
    p_a = _seed_provider(super_db_session, "pq_t14a")
    loc_a = _seed_location(super_db_session, p_a.id)
    rate_a = _seed_rate(super_db_session, p_a.id)
    pvg_a = _seed_pvg(super_db_session, p_a.id, loc_a.id, rate_a.id, "ECMR")
    run_a = _seed_scrape_run(super_db_session, p_a.id, loc_a.id, rate_a.id)
    rep_a = date(2026, 6, 15)
    _seed_zone(super_db_session, p_a.id, loc_a.id, rate_a.id, pvg_a.id,
               date(2026, 6, 1), date(2026, 7, 14), rep_a)
    _seed_observation(super_db_session, p_a.id, loc_a.id, rate_a.id, pvg_a.id, run_a.id,
                      rep_a, 7, Decimal("40.00"))

    # Provider B: two zones covering both tramos
    p_b = _seed_provider(super_db_session, "pq_t14b")
    loc_b = _seed_location(super_db_session, p_b.id)
    rate_b = _seed_rate(super_db_session, p_b.id)
    pvg_b = _seed_pvg(super_db_session, p_b.id, loc_b.id, rate_b.id, "ECMR")
    run_b = _seed_scrape_run(super_db_session, p_b.id, loc_b.id, rate_b.id)
    rep_b1 = date(2026, 6, 15)
    rep_b2 = date(2026, 7, 31)
    _seed_zone(super_db_session, p_b.id, loc_b.id, rate_b.id, pvg_b.id,
               date(2026, 6, 1), date(2026, 7, 14), rep_b1)
    _seed_zone(super_db_session, p_b.id, loc_b.id, rate_b.id, pvg_b.id,
               date(2026, 7, 15), date(2026, 8, 31), rep_b2)
    _seed_observation(super_db_session, p_b.id, loc_b.id, rate_b.id, pvg_b.id, run_b.id,
                      rep_b1, 7, Decimal("50.00"))
    _seed_observation(super_db_session, p_b.id, loc_b.id, rate_b.id, pvg_b.id, run_b.id,
                      rep_b2, 7, Decimal("55.00"))

    t = _seed_tenant(super_db_session)
    cvg = _seed_client_group(super_db_session, t.id)
    _seed_subscription(super_db_session, t.id, p_a.id, loc_a.id, rate_a.id)
    _seed_subscription(super_db_session, t.id, p_b.id, loc_b.id, rate_b.id)
    _seed_mapping(super_db_session, t.id, cvg.id, pvg_a.id)
    _seed_mapping(super_db_session, t.id, cvg.id, pvg_b.id)

    service = PriceQueryService(super_db_session)
    result = service.get_market_minimum_tariff(
        t.id,
        (date(2026, 6, 1), date(2026, 8, 31)),
        ["compact"],
        [7],
    )

    # Grid: (Jun 1 - Jul 14), (Jul 15 - Aug 31)
    assert len(result.rows) == 2
    rows_by_start = {r.period_start: r for r in result.rows}

    # Tramo 1: both providers → coverage=2, min(40, 50)=40
    row1 = rows_by_start[date(2026, 6, 1)]
    assert row1.coverage == 2
    assert row1.prices_by_duration[7] == Decimal("40.00")

    # Tramo 2: only Provider B → coverage=1, price=55
    row2 = rows_by_start[date(2026, 7, 15)]
    assert row2.coverage == 1
    assert row2.prices_by_duration[7] == Decimal("55.00")


def test_tenant_isolation_other_tenant_data_invisible(super_db_session, db_session):
    """RLS test: PriceQueryService with tenant_context for T1 must not see T2's data."""
    t1_id: uuid.UUID | None = None
    t2_id: uuid.UUID | None = None
    p_a_id: int | None = None
    p_b_id: int | None = None
    rep = date(2026, 6, 15)
    try:
        # Provider A → for Tenant 1
        p_a = _seed_provider(super_db_session, "pq_iso_a")
        loc_a = _seed_location(super_db_session, p_a.id, "ISO1")
        rate_a = _seed_rate(super_db_session, p_a.id)
        pvg_a = _seed_pvg(super_db_session, p_a.id, loc_a.id, rate_a.id, "ECMR")
        run_a = _seed_scrape_run(super_db_session, p_a.id, loc_a.id, rate_a.id)
        _seed_zone(super_db_session, p_a.id, loc_a.id, rate_a.id, pvg_a.id,
                   date(2026, 6, 1), date(2026, 8, 31), rep)
        _seed_observation(super_db_session, p_a.id, loc_a.id, rate_a.id, pvg_a.id, run_a.id,
                          rep, 7, Decimal("45.00"))
        p_a_id = p_a.id

        # Provider B → for Tenant 2
        p_b = _seed_provider(super_db_session, "pq_iso_b")
        loc_b = _seed_location(super_db_session, p_b.id, "ISO2")
        rate_b = _seed_rate(super_db_session, p_b.id)
        pvg_b = _seed_pvg(super_db_session, p_b.id, loc_b.id, rate_b.id, "ECMR")
        run_b = _seed_scrape_run(super_db_session, p_b.id, loc_b.id, rate_b.id)
        _seed_zone(super_db_session, p_b.id, loc_b.id, rate_b.id, pvg_b.id,
                   date(2026, 6, 1), date(2026, 8, 31), rep)
        _seed_observation(super_db_session, p_b.id, loc_b.id, rate_b.id, pvg_b.id, run_b.id,
                          rep, 7, Decimal("99.00"))
        p_b_id = p_b.id

        # Tenant 1 → subscribed to Provider A only
        t1 = _seed_tenant(super_db_session, "ISO Tenant 1")
        cvg1 = _seed_client_group(super_db_session, t1.id)
        _seed_subscription(super_db_session, t1.id, p_a.id, loc_a.id, rate_a.id)
        _seed_mapping(super_db_session, t1.id, cvg1.id, pvg_a.id)
        t1_id = t1.id

        # Tenant 2 → subscribed to Provider B only
        t2 = _seed_tenant(super_db_session, "ISO Tenant 2")
        cvg2 = _seed_client_group(super_db_session, t2.id)
        _seed_subscription(super_db_session, t2.id, p_b.id, loc_b.id, rate_b.id)
        _seed_mapping(super_db_session, t2.id, cvg2.id, pvg_b.id)
        t2_id = t2.id

        # Commit so db_session (separate connection) can see the data
        super_db_session.commit()

        # Query as Tenant 1 using the app role (RLS enforced)
        db_session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": str(t1_id)},
        )
        service = PriceQueryService(db_session)
        result = service.get_market_average_tariff(
            t1_id,
            (date(2026, 6, 1), date(2026, 8, 31)),
            ["compact"],
            [7],
        )

        # T1 should see only Provider A's data
        assert len(result.rows) == 1
        row = result.rows[0]
        assert row.coverage == 1  # only one subscription visible
        assert row.prices_by_duration[7] == Decimal("45.00")  # Provider A's price
        # Provider B's price (99.00) must not appear anywhere
        for r in result.rows:
            assert r.prices_by_duration.get(7) != Decimal("99.00")

    finally:
        _cleanup(
            super_db_session,
            provider_ids=[pid for pid in (p_a_id, p_b_id) if pid is not None],
            tenant_ids=[tid for tid in (t1_id, t2_id) if tid is not None],
        )
