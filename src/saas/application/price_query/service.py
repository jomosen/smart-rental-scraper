"""PriceQueryService — three Format A market-price queries.

The service receives its session by dependency injection; it never opens one
internally.  The caller controls the transaction scope.

RLS note
--------
tenant_subscriptions, client_vehicle_groups, and vehicle_group_mappings are
FORCE ROW LEVEL SECURITY tables.  When calling via the app role, the caller
must set app.tenant_id (e.g. via tenant_context()) before invoking any method.
Using super_session (BYPASSRLS) is valid for back-office use; application-layer
tenant_id filtering is always applied regardless of role.

Catalog tables (homogeneous_zones, price_observations) carry no RLS and are
readable by all roles without restriction.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ...infrastructure.persistence.models.catalog import (
    HomogeneousZone,
    Provider,
    ProviderLocation,
    ProviderRate,
    ProviderVehicleGroup,
)
from ...infrastructure.persistence.models.tenant import (
    ClientVehicleGroup,
    TenantSubscription,
    VehicleGroupMapping,
)
from .dtos import FormatARow, FormatATable, ZoneRange
from .rejilla import compute_intersected_grid

_DEFAULT_DURATIONS = (1, 2, 3, 4, 5, 6, 7, 14, 21, 28)


class PriceQueryService:
    """Read-only market-price query service for a single tenant."""

    def __init__(self, session: Session) -> None:
        self._s = session

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_provider_tariff(
        self,
        tenant_id: uuid.UUID,
        provider_code: str,
        location_code: str,
        rate_code: str,
        date_range: tuple[date, date],
        client_vehicle_group_codes: list[str],
        durations: list[int] = _DEFAULT_DURATIONS,
    ) -> FormatATable:
        """Format A prices from a single provider subscription.

        One row per (client_group × active zone) clipped to date_range.
        coverage is always None — the concept does not apply to a single
        provider.

        Returns an empty FormatATable (with a 'warning' key in metadata) when
        the tenant has no active subscription to (provider_code, location_code,
        rate_code), or any catalog lookup fails.
        """
        self._assert_session_tenant_consistent(tenant_id)
        d1, d2 = date_range
        metadata: dict = {
            "date_range": (d1.isoformat(), d2.isoformat()),
            "provider_code": provider_code,
            "location_code": location_code,
            "rate_code": rate_code,
        }

        # Resolve catalog IDs
        provider = self._s.scalar(
            select(Provider).where(Provider.code == provider_code)
        )
        if provider is None:
            metadata["warning"] = f"Provider '{provider_code}' not found in catalog."
            return FormatATable(rows=[], metadata=metadata)

        location = self._s.scalar(
            select(ProviderLocation).where(
                ProviderLocation.provider_id == provider.id,
                ProviderLocation.location_code == location_code,
            )
        )
        if location is None:
            metadata["warning"] = (
                f"Location '{location_code}' not found for provider '{provider_code}'."
            )
            return FormatATable(rows=[], metadata=metadata)

        rate = self._s.scalar(
            select(ProviderRate).where(
                ProviderRate.provider_id == provider.id,
                ProviderRate.rate_code == rate_code,
            )
        )
        if rate is None:
            metadata["warning"] = (
                f"Rate '{rate_code}' not found for provider '{provider_code}'."
            )
            return FormatATable(rows=[], metadata=metadata)

        pid, lid, rid = provider.id, location.id, rate.id

        # Verify active subscription
        sub = self._s.scalar(
            select(TenantSubscription).where(
                TenantSubscription.tenant_id == tenant_id,
                TenantSubscription.provider_id == pid,
                TenantSubscription.provider_location_id == lid,
                TenantSubscription.provider_rate_id == rid,
                TenantSubscription.status == "active",
            )
        )
        if sub is None:
            metadata["warning"] = (
                f"No active subscription for ({provider_code}, {location_code}, {rate_code})."
            )
            return FormatATable(rows=[], metadata=metadata)

        # Resolve client groups → UUIDs
        client_groups = self._resolve_client_groups(tenant_id, client_vehicle_group_codes)
        if not client_groups:
            return FormatATable(rows=[], metadata=metadata)

        # Mappings: client_vehicle_group_id → [pvg_id, ...]
        client_to_pvgs = self._resolve_mappings(
            tenant_id, list(client_groups.values()), pid, lid, rid
        )

        all_pvg_ids = sorted({pvg_id for ids in client_to_pvgs.values() for pvg_id in ids})
        if not all_pvg_ids:
            return FormatATable(rows=[], metadata=metadata)

        # Load active zones overlapping date_range
        zones = self._load_zones(pid, lid, rid, all_pvg_ids, d1, d2)
        if not zones:
            return FormatATable(rows=[], metadata=metadata)

        # Group zones by pvg_id for efficient lookup
        pvg_zones: dict[int, list] = {}
        for z in zones:
            pvg_zones.setdefault(z.provider_vehicle_group_id, []).append(z)

        rep_dates = list({z.representative_date for z in zones})
        obs = self._fetch_observations(pid, lid, rid, all_pvg_ids, rep_dates, list(durations))

        # Build rows: one per (client_group × distinct zone covering that group's PVGs)
        rows: list[FormatARow] = []
        for cvg_code, cvg_id in client_groups.items():
            pvg_ids_for_group = client_to_pvgs.get(cvg_id, [])
            if not pvg_ids_for_group:
                continue

            # Collect distinct zones that have at least one PVG for this client group
            group_zones: dict[tuple, object] = {}
            for pvg_id in pvg_ids_for_group:
                for z in pvg_zones.get(pvg_id, []):
                    key = (z.start_date, z.end_date, z.representative_date)
                    if key not in group_zones:
                        group_zones[key] = z

            for z in group_zones.values():
                period_start = max(z.start_date, d1)
                period_end = min(z.end_date, d2)

                prices_by_duration: dict[int, Optional[Decimal]] = {}
                for dur in durations:
                    pvg_prices = [
                        obs[(pvg_id, z.representative_date, dur)]
                        for pvg_id in pvg_ids_for_group
                        if (pvg_id, z.representative_date, dur) in obs
                    ]
                    prices_by_duration[dur] = min(pvg_prices) if pvg_prices else None

                rows.append(FormatARow(
                    client_group_code=cvg_code,
                    period_start=period_start,
                    period_end=period_end,
                    prices_by_duration=prices_by_duration,
                    coverage_by_duration=None,
                ))

        rows.sort(key=lambda r: (r.client_group_code, r.period_start))
        metadata["currency"] = provider.default_currency
        return FormatATable(rows=rows, metadata=metadata)

    def get_market_average_tariff(
        self,
        tenant_id: uuid.UUID,
        date_range: tuple[date, date],
        client_vehicle_group_codes: list[str],
        durations: list[int] = _DEFAULT_DURATIONS,
    ) -> FormatATable:
        """Format A prices averaged across all active subscriptions.

        One row per (client_group × tramo from the intersected grid).
        coverage = number of subscriptions contributing at least one price to
        that row.  Rows with coverage=0 appear with prices_by_duration all None.

        Session scope: app.tenant_id must be set via tenant_context(), or use
        super_session (BYPASSRLS).
        """
        return self._get_market_tariff(
            "average", tenant_id, date_range, client_vehicle_group_codes, durations
        )

    def get_market_minimum_tariff(
        self,
        tenant_id: uuid.UUID,
        date_range: tuple[date, date],
        client_vehicle_group_codes: list[str],
        durations: list[int] = _DEFAULT_DURATIONS,
    ) -> FormatATable:
        """Format A prices as minimum across all active subscriptions.

        One row per (client_group × tramo from the intersected grid).
        coverage = number of subscriptions contributing at least one price to
        that row.  Rows with coverage=0 appear with prices_by_duration all None.

        Session scope: app.tenant_id must be set via tenant_context(), or use
        super_session (BYPASSRLS).
        """
        return self._get_market_tariff(
            "minimum", tenant_id, date_range, client_vehicle_group_codes, durations
        )

    # ── Shared aggregate implementation ────────────────────────────────────────

    def _get_market_tariff(
        self,
        aggregate: str,  # "average" or "minimum"
        tenant_id: uuid.UUID,
        date_range: tuple[date, date],
        client_vehicle_group_codes: list[str],
        durations: list[int],
    ) -> FormatATable:
        self._assert_session_tenant_consistent(tenant_id)
        d1, d2 = date_range
        metadata: dict = {
            "date_range": (d1.isoformat(), d2.isoformat()),
            "aggregate": aggregate,
        }

        # Active subscriptions for this tenant
        active_subs = self._s.scalars(
            select(TenantSubscription).where(
                TenantSubscription.tenant_id == tenant_id,
                TenantSubscription.status == "active",
            )
        ).all()
        if not active_subs:
            return FormatATable(rows=[], metadata=metadata)

        client_groups = self._resolve_client_groups(tenant_id, client_vehicle_group_codes)
        if not client_groups:
            return FormatATable(rows=[], metadata=metadata)

        # Build per-subscription context
        sub_data: list[dict] = []
        zones_by_sub: dict[int, list[ZoneRange]] = {}  # sub_index → zone ranges

        for sub_idx, sub in enumerate(active_subs):
            pid = sub.provider_id
            lid = sub.provider_location_id
            rid = sub.provider_rate_id

            client_to_pvgs = self._resolve_mappings(
                tenant_id, list(client_groups.values()), pid, lid, rid
            )
            all_pvg_ids = sorted({pvg_id for ids in client_to_pvgs.values() for pvg_id in ids})
            if not all_pvg_ids:
                continue

            zones = self._load_zones(pid, lid, rid, all_pvg_ids, d1, d2)
            if not zones:
                continue

            # pvg_id → list of (start, end, rep) for zone lookups
            pvg_zone_map: dict[int, list[tuple[date, date, date]]] = {}
            for z in zones:
                pvg_zone_map.setdefault(z.provider_vehicle_group_id, []).append(
                    (z.start_date, z.end_date, z.representative_date)
                )

            zones_by_sub[sub_idx] = [ZoneRange(z.start_date, z.end_date) for z in zones]

            rep_dates = list({z.representative_date for z in zones})
            obs = self._fetch_observations(
                pid, lid, rid, all_pvg_ids, rep_dates, list(durations)
            )

            sub_data.append({
                "client_to_pvgs": client_to_pvgs,   # cvg_id → [pvg_id]
                "pvg_zone_map": pvg_zone_map,        # pvg_id → [(start, end, rep)]
                "obs": obs,                          # (pvg_id, date, dur) → price
            })

        if not sub_data:
            return FormatATable(rows=[], metadata=metadata)

        grid = compute_intersected_grid(zones_by_sub, date_range)
        if not grid:
            return FormatATable(rows=[], metadata=metadata)

        # Build rows: one per (client_group × tramo)
        rows: list[FormatARow] = []
        for cvg_code, cvg_id in client_groups.items():
            for T1, T2 in grid:
                sub_prices_by_dur: dict[int, list[Decimal]] = {d: [] for d in durations}
                coverage_per_duration: dict[int, int] = {d: 0 for d in durations}

                for sd in sub_data:
                    pvg_ids = sd["client_to_pvgs"].get(cvg_id, [])
                    if not pvg_ids:
                        continue

                    pvg_zone_map = sd["pvg_zone_map"]
                    obs = sd["obs"]

                    # N:M min policy: per duration, collect prices across PVGs then take min
                    sub_dur_prices: dict[int, list[Decimal]] = {d: [] for d in durations}
                    for pvg_id in pvg_ids:
                        rep = _find_representative(pvg_zone_map.get(pvg_id, []), T1, T2)
                        if rep is None:
                            continue
                        for dur in durations:
                            price = obs.get((pvg_id, rep, dur))
                            if price is not None:
                                sub_dur_prices[dur].append(price)

                    for dur in durations:
                        if sub_dur_prices[dur]:
                            sub_prices_by_dur[dur].append(min(sub_dur_prices[dur]))
                            coverage_per_duration[dur] += 1

                # Aggregate across subscriptions
                prices_by_duration: dict[int, Optional[Decimal]] = {}
                for dur in durations:
                    dur_prices = sub_prices_by_dur[dur]
                    if not dur_prices:
                        prices_by_duration[dur] = None
                    elif aggregate == "average":
                        avg = sum(dur_prices) / len(dur_prices)
                        prices_by_duration[dur] = Decimal(avg).quantize(Decimal("0.01"))
                    else:
                        prices_by_duration[dur] = min(dur_prices)

                rows.append(FormatARow(
                    client_group_code=cvg_code,
                    period_start=T1,
                    period_end=T2,
                    prices_by_duration=prices_by_duration,
                    coverage_by_duration=coverage_per_duration,
                ))

        rows.sort(key=lambda r: (r.client_group_code, r.period_start))
        return FormatATable(rows=rows, metadata=metadata)

    # ── Private helpers ─────────────────────────────────────────────────────────

    def _assert_session_tenant_consistent(self, tenant_id: uuid.UUID) -> None:
        """Validate that the session's app.tenant_id matches the requested
        tenant_id, OR that the session has no tenant context (BYPASSRLS).

        Raises ValueError on mismatch — common cause is calling the service
        with a session whose app.tenant_id was set to a different tenant.
        """
        result = self._s.execute(
            text("SELECT current_setting('app.tenant_id', true)")
        ).scalar()
        if result and result.strip() and result != str(tenant_id):
            raise ValueError(
                f"Session app.tenant_id ({result!r}) does not match the "
                f"requested tenant_id ({tenant_id}). Caller likely set the "
                "session context to a different tenant."
            )

    def _resolve_client_groups(
        self,
        tenant_id: uuid.UUID,
        codes: list[str],
    ) -> dict[str, uuid.UUID]:
        """Return {code: client_vehicle_group_id} for the requested codes."""
        if not codes:
            return {}
        groups = self._s.scalars(
            select(ClientVehicleGroup).where(
                ClientVehicleGroup.tenant_id == tenant_id,
                ClientVehicleGroup.code.in_(codes),
            )
        ).all()
        return {g.code: g.id for g in groups}

    def _resolve_mappings(
        self,
        tenant_id: uuid.UUID,
        cvg_ids: list[uuid.UUID],
        provider_id: int,
        location_id: int,
        rate_id: int,
    ) -> dict[uuid.UUID, list[int]]:
        """Return {cvg_id: [pvg_id, ...]} for the given (provider, location, rate) tuple.

        Only active provider_vehicle_groups are considered.
        """
        if not cvg_ids:
            return {}

        pvgs = self._s.scalars(
            select(ProviderVehicleGroup).where(
                ProviderVehicleGroup.provider_id == provider_id,
                ProviderVehicleGroup.provider_location_id == location_id,
                ProviderVehicleGroup.provider_rate_id == rate_id,
                ProviderVehicleGroup.active == True,
            )
        ).all()
        pvg_ids_for_tuple = [pvg.id for pvg in pvgs]
        if not pvg_ids_for_tuple:
            return {}

        mappings = self._s.scalars(
            select(VehicleGroupMapping).where(
                VehicleGroupMapping.tenant_id == tenant_id,
                VehicleGroupMapping.client_vehicle_group_id.in_(cvg_ids),
                VehicleGroupMapping.provider_vehicle_group_id.in_(pvg_ids_for_tuple),
            )
        ).all()

        result: dict[uuid.UUID, list[int]] = {}
        for m in mappings:
            result.setdefault(m.client_vehicle_group_id, []).append(
                m.provider_vehicle_group_id
            )
        return result

    def _load_zones(
        self,
        provider_id: int,
        location_id: int,
        rate_id: int,
        pvg_ids: list[int],
        d1: date,
        d2: date,
    ) -> list:
        """Load active homogeneous zones overlapping [d1, d2] for the given PVGs."""
        if not pvg_ids:
            return []
        return self._s.scalars(
            select(HomogeneousZone).where(
                HomogeneousZone.provider_id == provider_id,
                HomogeneousZone.provider_location_id == location_id,
                HomogeneousZone.provider_rate_id == rate_id,
                HomogeneousZone.provider_vehicle_group_id.in_(pvg_ids),
                HomogeneousZone.active == True,
                HomogeneousZone.end_date >= d1,
                HomogeneousZone.start_date <= d2,
            )
        ).all()

    def _fetch_observations(
        self,
        provider_id: int,
        location_id: int,
        rate_id: int,
        pvg_ids: list[int],
        rep_dates: list[date],
        durations: list[int],
    ) -> dict[tuple[int, date, int], Decimal]:
        """Fetch the latest price_per_day for each (pvg, pickup_date, duration) tuple.

        Uses DISTINCT ON with the canonical index order from DATA_MODEL.md Part 3:
        (provider_id, location_id, rate_id, pvg_id, pickup_date, duration, observed_at DESC).
        """
        if not pvg_ids or not rep_dates:
            return {}
        rows = self._s.execute(
            text("""
                SELECT DISTINCT ON (provider_vehicle_group_id, pickup_date, duration_days)
                       provider_vehicle_group_id,
                       pickup_date,
                       duration_days,
                       price_per_day
                FROM price_observations
                WHERE provider_id          = :pid
                  AND provider_location_id = :lid
                  AND provider_rate_id     = :rid
                  AND provider_vehicle_group_id = ANY(:pvg_ids)
                  AND pickup_date          = ANY(:rep_dates)
                  AND duration_days        = ANY(:durations)
                ORDER BY provider_vehicle_group_id,
                         pickup_date,
                         duration_days,
                         observed_at DESC
            """),
            {
                "pid": provider_id,
                "lid": location_id,
                "rid": rate_id,
                "pvg_ids": pvg_ids,
                "rep_dates": rep_dates,
                "durations": durations,
            },
        )
        return {
            (row.provider_vehicle_group_id, row.pickup_date, row.duration_days): row.price_per_day
            for row in rows
        }


# ── Module-level pure helper ────────────────────────────────────────────────────

def _find_representative(
    zone_list: list[tuple[date, date, date]],  # [(start, end, rep), ...]
    T1: date,
    T2: date,
) -> Optional[date]:
    """Return the representative_date of the zone covering [T1, T2].

    PRECONDITION: [T1, T2] must be a tramo of the intersected grid
    produced by compute_intersected_grid. By construction such a
    tramo lies entirely within at most one zone per PVG.

    Calling with arbitrary [T1, T2] that crosses zone boundaries
    returns None silently — this helper is internal to the
    market-aggregate methods and must not be reused outside that
    context.
    """
    for start, end, rep in zone_list:
        if start <= T1 and end >= T2:
            return rep
    return None
