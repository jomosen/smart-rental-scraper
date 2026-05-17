"""Onboarding step functions.

Each function performs one discrete step of the tenant onboarding sequence.
Steps accept an open session rather than opening one themselves so that the
caller (CLI or test) controls transaction scope.

Steps that need BYPASSRLS (1, 2): pass a super-role session.
Steps that write tenant-scoped rows (4-7): pass an app-role session with
app.tenant_id already set via tenant_context(), or a super-role session in
tests (BYPASSRLS, no RLS enforcement).

The discovery step (3) is the exception: it opens its own sessions via the
provided session_factory because it may delegate to SmartScraperOrchestrator.
Cross-boundary imports (saas → scraper) are isolated in discovery.py.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...infrastructure.persistence.models.catalog import (
    Provider,
    ProviderLocation,
    ProviderRate,
    ProviderVehicleCategory,
    ScrapeRun,
)
from ...infrastructure.persistence.models.tenant import (
    Tenant,
    TenantSubscription,
    TenantVehicleGroup,
    TenantVehicleGroupMapping,
    User,
)
from ...infrastructure.persistence.repositories import ProviderVehicleCategoryRepository
from .. import discovery
from .config import OnboardingConfig


class OnboardingError(RuntimeError):
    """Raised when a step fails due to a correctable state, not a bug."""


# ---------------------------------------------------------------------------
# Step 1 — validate catalog
# ---------------------------------------------------------------------------

def step_validate_catalog(
    config: OnboardingConfig,
    session: Session,
) -> dict[tuple[str, str, str], tuple[int, int, int]]:
    """Check that every subscribed (provider, location, rate) exists in catalog.

    Returns {(provider_code, location_code, rate_code): (provider_id, location_id, rate_id)}.
    Multiple subscriptions to the same provider_code are supported as long as
    the (provider_code, location_code, rate_code) tuple is unique.
    """
    catalog_ids: dict[tuple[str, str, str], tuple[int, int, int]] = {}

    for sub in config.subscriptions:
        provider = session.scalar(
            select(Provider).where(Provider.code == sub.provider_code)
        )
        if provider is None:
            raise OnboardingError(
                f"Provider '{sub.provider_code}' not found in catalog. "
                "Run the scraper pipeline at least once to populate the catalog."
            )

        location = session.scalar(
            select(ProviderLocation).where(
                ProviderLocation.provider_id == provider.id,
                ProviderLocation.location_code == sub.location_code,
            )
        )
        if location is None:
            raise OnboardingError(
                f"Location '{sub.location_code}' not found for provider '{sub.provider_code}'."
            )

        rate = session.scalar(
            select(ProviderRate).where(
                ProviderRate.provider_id == provider.id,
                ProviderRate.rate_code == sub.rate_code,
            )
        )
        if rate is None:
            raise OnboardingError(
                f"Rate '{sub.rate_code}' not found for provider '{sub.provider_code}'."
            )

        key = (sub.provider_code, sub.location_code, sub.rate_code)
        catalog_ids[key] = (provider.id, location.id, rate.id)

    return catalog_ids


# ---------------------------------------------------------------------------
# Step 2 — create tenant
# ---------------------------------------------------------------------------

def step_create_tenant(
    config: OnboardingConfig,
    session: Session,
) -> uuid.UUID:
    """Insert a Tenant row and flush to materialise its UUID. Caller must commit.

    Raises OnboardingError if a tenant with the same name already exists, so
    that re-running onboarding for an existing tenant fails loud rather than
    silently creating a duplicate.
    """
    existing = session.scalar(select(Tenant).where(Tenant.name == config.tenant.name))
    if existing is not None:
        raise OnboardingError(
            f"A tenant named '{config.tenant.name}' already exists (id={existing.id}). "
            "Choose a different name or use --tenant-id to resume an interrupted run."
        )
    tenant = Tenant(
        name=config.tenant.name,
        currency=config.tenant.currency,
        plan="mvp",
    )
    session.add(tenant)
    session.flush()
    return tenant.id


# ---------------------------------------------------------------------------
# Step 3 — discovery
# ---------------------------------------------------------------------------

def step_discovery(
    catalog_ids: dict[tuple[str, str, str], tuple[int, int, int]],
    providers_json: list[dict],
    session_factory: Callable,
    period_start: datetime,
    period_end: datetime,
    scraper_factory: Callable,
    pickup_hour: int = 10,
) -> None:
    """Ensure provider_vehicle_groups exist for every subscribed tuple.

    For each (provider_code, location_code, rate_code) in catalog_ids, checks
    whether a successful ScrapeRun already exists. If not, calls
    discovery.run_discovery_for_tuple() to run the scraper inline.

    scraper_factory is required — pass the result of discovery.build_scraper_factory().
    Cross-boundary imports are contained in src/saas/application/discovery.py.
    """
    entry_by_code: dict[str, dict] = {
        e["scraper"]: e for e in providers_json if e.get("enabled", True)
    }

    for (pcode, lcode, rcode), (pid, lid, rid) in catalog_ids.items():
        with session_factory() as s:
            existing = s.scalar(
                select(ScrapeRun).where(
                    ScrapeRun.provider_id == pid,
                    ScrapeRun.provider_location_id == lid,
                    ScrapeRun.provider_rate_id == rid,
                    ScrapeRun.status == "success",
                )
            )
        if existing is not None:
            continue

        entry = entry_by_code.get(pcode)
        if entry is None:
            raise OnboardingError(
                f"No entry for '{pcode}' in providers_json — "
                "cannot run discovery without scraper configuration."
            )

        discovery.run_discovery_for_tuple(
            provider_code=pcode,
            location_code=lcode,
            rate_code=rcode,
            provider_id=pid,
            provider_location_id=lid,
            provider_rate_id=rid,
            providers_json_entry=entry,
            session_factory=session_factory,
            period_start=period_start,
            period_end=period_end,
            pickup_hour=pickup_hour,
            scraper_factory=scraper_factory,
        )


# ---------------------------------------------------------------------------
# Step 4 — create owner user and client vehicle groups
# ---------------------------------------------------------------------------

def step_create_users_and_groups(
    config: OnboardingConfig,
    tenant_id: uuid.UUID,
    session: Session,
) -> dict[str, uuid.UUID]:
    """Create the owner User and all TenantVehicleGroup rows.

    Returns {vehicle_group_code: tenant_vehicle_group_id}.
    Warns to stderr that external_auth_id is NULL until an identity provider is configured.
    """
    print(
        f"[warning] User '{config.tenant.owner_email}' created with "
        "external_auth_id=NULL. Set external_auth_id when the identity provider is configured.",
        file=sys.stderr,
    )
    user = User(
        tenant_id=tenant_id,
        email=config.tenant.owner_email,
        role="owner",
    )
    session.add(user)
    session.flush()

    client_group_ids: dict[str, uuid.UUID] = {}
    for grp in config.vehicle_groups:
        tvg = TenantVehicleGroup(
            tenant_id=tenant_id,
            code=grp.code,
            name=grp.name,
            description=grp.description,
            display_order=grp.display_order,
        )
        session.add(tvg)
        session.flush()
        client_group_ids[grp.code] = tvg.id

    return client_group_ids


# ---------------------------------------------------------------------------
# Step 5 — create vehicle group mappings
# ---------------------------------------------------------------------------

def step_create_mappings(
    config: OnboardingConfig,
    catalog_ids: dict[tuple[str, str, str], tuple[int, int, int]],
    tenant_id: uuid.UUID,
    client_group_ids: dict[str, uuid.UUID],
    session: Session,
) -> int:
    """Create TenantVehicleGroupMapping rows for every (tenant_group, acriss_code) pair.

    Iterates over subscriptions. Each subscription's mappings reference a
    client_group_code (vehicle group on the tenant side) and a list of provider
    external_codes for that subscription's (provider, location, rate) tuple.

    Returns the total number of mapping rows created.
    Raises OnboardingError if a referenced provider_vehicle_category does not exist.
    """
    repo = ProviderVehicleCategoryRepository(session)
    count = 0

    for sub in config.subscriptions:
        key = (sub.provider_code, sub.location_code, sub.rate_code)
        pid, lid, rid = catalog_ids[key]
        for mapping in sub.mappings:
            tvg_id = client_group_ids[mapping.client_group_code]
            for ext_code in mapping.external_codes:
                pvc = repo.get_by_external_code(pid, lid, rid, ext_code)
                if pvc is None:
                    raise OnboardingError(
                        f"provider_vehicle_category not found: "
                        f"({sub.provider_code}, {sub.location_code}, {sub.rate_code}) "
                        f"external_code='{ext_code}'. "
                        "Has discovery (step 3) completed for this provider?"
                    )
                if pvc.acriss_code is None:
                    raise OnboardingError(
                        f"provider_vehicle_category '{ext_code}' for "
                        f"({sub.provider_code}, {sub.location_code}, {sub.rate_code}) "
                        "exists but has not yet been assigned an ACRISS code. "
                        "Onboarding requires that all referenced provider vehicle "
                        "categories have been classified. Run a scrape with the "
                        "ClassificationService enabled before onboarding tenants, "
                        "or manually classify the PVC in the database."
                    )
                vm = TenantVehicleGroupMapping(
                    tenant_id=tenant_id,
                    tenant_vehicle_group_id=tvg_id,
                    acriss_code=pvc.acriss_code,
                )
                session.add(vm)
                count += 1

    session.flush()
    return count


# ---------------------------------------------------------------------------
# Step 6 — create subscriptions
# ---------------------------------------------------------------------------

def step_create_subscriptions(
    config: OnboardingConfig,
    catalog_ids: dict[tuple[str, str, str], tuple[int, int, int]],
    tenant_id: uuid.UUID,
    session: Session,
) -> dict[tuple[str, str, str], uuid.UUID]:
    """Create TenantSubscription rows with status='pending_mapping'.

    Returns {(provider_code, location_code, rate_code): subscription_id}.
    """
    sub_ids: dict[tuple[str, str, str], uuid.UUID] = {}
    for sub in config.subscriptions:
        key = (sub.provider_code, sub.location_code, sub.rate_code)
        pid, lid, rid = catalog_ids[key]
        ts = TenantSubscription(
            tenant_id=tenant_id,
            provider_id=pid,
            provider_location_id=lid,
            provider_rate_id=rid,
            status="pending_mapping",
        )
        session.add(ts)
        session.flush()
        sub_ids[key] = ts.id
    return sub_ids


# ---------------------------------------------------------------------------
# Step 7 — activate subscriptions
# ---------------------------------------------------------------------------

def step_activate_subscriptions(
    subscription_ids_by_tuple: dict[tuple[str, str, str], uuid.UUID],
    catalog_ids: dict[tuple[str, str, str], tuple[int, int, int]],
    tenant_id: uuid.UUID,
    session: Session,
) -> dict[tuple[str, str, str], str]:
    """Attempt to transition each subscription from pending_mapping → active.

    A subscription becomes active when at least one ACRISS code
    for the tuple (via an active ProviderVehicleCategory with an acriss_code)
    has a TenantVehicleGroupMapping in this tenant.  PVCs without an
    acriss_code are excluded — classification has not yet been run for them.

    Subscriptions with zero mapped ACRISS codes stay in 'pending_mapping'.
    Subscriptions with partial mappings are activated; an [info] is printed
    listing the unmapped ACRISS codes so the operator is aware.

    Returns {(provider_code, location_code, rate_code): 'active' | 'pending_mapping'}.
    """
    # One query: all acriss_codes already mapped for this tenant
    mapped_acriss_codes: set[str] = set(
        session.scalars(
            select(TenantVehicleGroupMapping.acriss_code).where(
                TenantVehicleGroupMapping.tenant_id == tenant_id,
            )
        ).all()
    )

    statuses: dict[tuple[str, str, str], str] = {}

    for key, sub_id in subscription_ids_by_tuple.items():
        pcode, lcode, rcode = key
        pid, lid, rid = catalog_ids[key]

        active_acriss_codes: set[str] = set(
            session.scalars(
                select(ProviderVehicleCategory.acriss_code).where(
                    ProviderVehicleCategory.provider_id == pid,
                    ProviderVehicleCategory.provider_location_id == lid,
                    ProviderVehicleCategory.provider_rate_id == rid,
                    ProviderVehicleCategory.active == True,
                    ProviderVehicleCategory.acriss_code.is_not(None),
                )
            ).all()
        )

        unmapped = active_acriss_codes - mapped_acriss_codes
        sub_mapped_codes = mapped_acriss_codes & active_acriss_codes

        if not sub_mapped_codes:
            print(
                f"[warning] Subscription ({pcode}, {lcode}, {rcode}) has no "
                "mappings for this tuple — leaving in 'pending_mapping'. "
                "Add at least one mapping to enable queries for this subscription.",
                file=sys.stderr,
            )
            statuses[key] = "pending_mapping"
        else:
            if unmapped:
                print(
                    f"[info] Subscription ({pcode}, {lcode}, {rcode}) has "
                    f"{len(unmapped)} unmapped active ACRISS code(s): "
                    f"{', '.join(sorted(unmapped))}. "
                    "These codes are not in this tenant's scope and won't "
                    "appear in its queries. If that's intentional, ignore this notice.",
                    file=sys.stderr,
                )
            ts = session.get(TenantSubscription, sub_id)
            ts.status = "active"
            statuses[key] = "active"

    return statuses
