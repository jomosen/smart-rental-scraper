"""Provider-groups catalog API (v1) — machine-to-machine.

The counterpart of /api/v1/prices for the matching flow: that endpoint answers
"what do these groups cost", this one answers "what groups exist and how far
ahead do they have prices". An external system uses it to build its matching
selector (anchor + fallback pickers with coverage calendars) and to validate
its stored mappings against the current catalog.

Authenticated by tenant API key (`Authorization: Bearer <key>` or `X-API-Key`),
like the rest of /api/v1. The catalog itself is global (providers and their
groups are catalog tables, not tenant-scoped); the key gates access and
resolves the tenant, it does not filter rows.
"""
from __future__ import annotations

import datetime
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.saas.infrastructure.persistence.engine import app_engine
from src.saas.infrastructure.persistence.read.catalog_read import (
    empty_coverage,
    fetch_group_coverage,
    fetch_provider_groups,
)
from src.saas.infrastructure.persistence.read.cross_tariff_read import (
    DURATIONS,
    fetch_location,
)
from src.saas.infrastructure.persistence.session import make_session_factory, tenant_context

from ..dependencies import get_tenant_from_api_key

router = APIRouter()


@router.get("/api/v1/provider-groups")
def get_provider_groups(
    provider: Optional[str] = Query(
        default=None, description="Restrict to one provider code (e.g. 'centauro')."
    ),
    location_id: Optional[int] = Query(
        default=None, description="Restrict to groups offered in one canonical market."
    ),
    duration: int = Query(
        default=7,
        description="Reference duration (days) for the coverage calendar.",
    ),
    tenant_id: uuid.UUID = Depends(get_tenant_from_api_key),
) -> dict:
    """List every active vehicle group of the active providers, with coverage.

    One entry per logical group: a group offered at three offices is one entry
    with three `location_ids`, not three entries. `group_key` is the stable
    identifier to persist when referencing a group. Groups pending
    classification are included with `acriss_code: null` — matching a group
    directly does not require it to be classified.

    Coverage is computed against today on every request (never cached): the
    seasons ahead (`ranges`, flagged priced/unpriced for the reference
    duration), the day counts (`covered_days` vs `horizon_days` — they differ
    when seasons exist without a backing price), and `by_duration`, because a
    group's coverage at 7 days can differ from its coverage at 28.
    """
    if duration not in DURATIONS:
        raise HTTPException(
            status_code=422,
            detail={"error": f"duration must be one of {list(DURATIONS)}"},
        )

    today = datetime.date.today()
    provider_codes = (provider,) if provider else None
    factory = make_session_factory(app_engine())
    with tenant_context(factory, tenant_id) as session:
        if location_id is not None and fetch_location(session, location_id) is None:
            raise HTTPException(status_code=404, detail={"error": "Location not found"})

        groups = fetch_provider_groups(
            session, provider_codes=provider_codes, location_id=location_id
        )
        coverage = fetch_group_coverage(
            session,
            reference_duration=duration,
            today=today,
            provider_codes=provider_codes,
            location_id=location_id,
        )

    for g in groups:
        g["coverage"] = coverage.get(
            (g["provider_code"], g["group_key"]), empty_coverage(today)
        )

    return {
        "provider": provider,
        "location_id": location_id,
        "duration": duration,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "total": len(groups),
        "groups": groups,
    }
