"""Tenant rollback: remove all rows created for a tenant in FK-safe order.

Must be called with a session connected as smart_rental_super (BYPASSRLS) so
that FORCE ROW LEVEL SECURITY on the tenant tables does not block the deletes.
The caller is responsible for committing — this function does not commit.
"""
from __future__ import annotations

import uuid

from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from ...infrastructure.persistence.models.tenant import (
    PricingOutput,
    PricingRule,
    Tenant,
    TenantSubscription,
    TenantVehicleGroup,
    TenantVehicleGroupMapping,
    User,
)


def rollback_tenant(tenant_id: uuid.UUID, session: Session) -> None:
    """Delete every row owned by tenant_id, in FK-safe order.

    Idempotent: silently succeeds if the tenant was never created or is
    already gone.
    """
    # 1. Vehicle group mappings (FK → tenant_vehicle_groups, acriss_codes)
    session.execute(
        delete(TenantVehicleGroupMapping).where(TenantVehicleGroupMapping.tenant_id == tenant_id)
    )
    # 2. Subscriptions (FK → tenant, providers, …)
    session.execute(
        delete(TenantSubscription).where(TenantSubscription.tenant_id == tenant_id)
    )
    # 3. Pricing outputs (FK → pricing_rules, which we delete next)
    session.execute(
        delete(PricingOutput).where(PricingOutput.tenant_id == tenant_id)
    )
    # 4. Null the self-FK before deleting pricing_rules to avoid RESTRICT violation
    session.execute(
        update(PricingRule)
        .where(PricingRule.tenant_id == tenant_id)
        .values(superseded_by_id=None)
    )
    session.execute(
        delete(PricingRule).where(PricingRule.tenant_id == tenant_id)
    )
    # 5. Tenant vehicle groups (FK → tenant; referenced by tenant_vehicle_group_mappings already gone)
    session.execute(
        delete(TenantVehicleGroup).where(TenantVehicleGroup.tenant_id == tenant_id)
    )
    # 6. Users (FK → tenant)
    session.execute(
        delete(User).where(User.tenant_id == tenant_id)
    )
    # 7. Tenant root row
    session.execute(
        delete(Tenant).where(Tenant.id == tenant_id)
    )
