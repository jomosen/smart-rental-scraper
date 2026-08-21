"""Catalog API — the provider vehicle groups available for matching.

`/api/cross-tariff` answers "what do these groups cost in the rendered zone";
this answers "what groups exist at all". A matching UI needs the second: the
full catalog, including groups with no price in the zone or duration currently
on screen, and groups that classification left unclassified.

Session-cookie auth, like the rest of the dashboard API. The group catalog is
global (providers and their vehicle groups are catalog tables, not tenant-scoped),
so the tenant context here gates access rather than filtering rows.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.saas.infrastructure.persistence.engine import app_engine
from src.saas.infrastructure.persistence.read.cross_tariff_read import (
    fetch_location,
    fetch_provider_groups,
)
from src.saas.infrastructure.persistence.session import make_session_factory, tenant_context

from ..dependencies import get_current_tenant

router = APIRouter()


@router.get("/api/provider-groups")
def get_provider_groups(
    provider: Optional[str] = Query(
        default=None, description="Restrict to one provider code (e.g. 'centauro')."
    ),
    location_id: Optional[int] = Query(
        default=None, description="Restrict to groups offered in one canonical market."
    ),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
) -> dict:
    """List every active vehicle group of the active providers.

    One entry per logical group: a group offered at three offices is one entry
    with three `location_ids`, not three entries.

    `group_key` is the stable identifier to persist when referencing a group —
    the provider's own `external_code`, or `attributes_hash` for providers that
    expose no codes.

    Groups pending classification are included with `acriss_code: null`; matching
    a group directly does not require it to be classified.
    """
    factory = make_session_factory(app_engine())
    with tenant_context(factory, tenant_id) as session:
        if location_id is not None and fetch_location(session, location_id) is None:
            raise HTTPException(status_code=404, detail={"error": "Location not found"})

        groups = fetch_provider_groups(
            session,
            provider_codes=(provider,) if provider else None,
            location_id=location_id,
        )

    return {
        "provider": provider,
        "location_id": location_id,
        "total": len(groups),
        "groups": groups,
    }
