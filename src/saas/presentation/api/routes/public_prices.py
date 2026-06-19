"""Public prices API (v1) — machine-to-machine.

A read-only JSON endpoint that returns the tenant's **final configured prices**
(its active pricing rule already applied), by ACRISS code × zone × duration —
the same data behind the dashboard's CSV/PDF export, but as JSON and
authenticated by a per-tenant API key instead of a browser session cookie.

External systems (e.g. the client's booking engine cron) pull this to copy the
prices into their own system. Authentication: `Authorization: Bearer <api-key>`
(or `X-API-Key`). See api/dependencies.get_tenant_from_api_key.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from src.saas.infrastructure.persistence.engine import app_engine
from src.saas.infrastructure.persistence.session import make_session_factory, tenant_context

from ..dependencies import get_tenant_from_api_key
# Reuse the dashboard's priced-rows builder and the canonical duration set so the
# API and the dashboard/export never drift.
from .cross_tariff import DURATIONS, _export_result

router = APIRouter()


def _money(value: Optional[Decimal]) -> Optional[float]:
    return float(value) if value is not None else None


def _serialize(result, tenant_name: str, currency: str, location_id: Optional[int]) -> dict:
    """Group ExportRows into one object per (acriss_code, zone) with price maps."""
    groups: dict[tuple, dict] = {}
    order: list[tuple] = []
    for r in result.rows:
        key = (r.acriss_code, r.zone_index)
        g = groups.get(key)
        if g is None:
            g = {
                "acriss_code": r.acriss_code,
                "category": r.categoria,
                "zone": {
                    "index": r.zone_index,
                    "date_from": r.zone_desde.isoformat() if r.zone_desde else None,
                    "date_to": r.zone_hasta.isoformat() if r.zone_hasta else None,
                },
                "prices_per_day": {},
                "prices_total": {},
            }
            groups[key] = g
            order.append(key)
        g["prices_per_day"][str(r.duracion_dias)] = _money(r.recomendado_per_day)
        g["prices_total"][str(r.duracion_dias)] = _money(r.recomendado_total)

    return {
        "tenant": tenant_name,
        "currency": currency,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "location_id": location_id,
        "durations": list(DURATIONS),
        "total_zones": result.total_zones,
        "prices": [groups[k] for k in order],
    }


@router.get("/api/v1/prices")
def get_prices(
    location_id: Optional[int] = Query(default=None),
    zone_from: Optional[int] = Query(default=None),
    zone_to: Optional[int] = Query(default=None),
    tenant_id: uuid.UUID = Depends(get_tenant_from_api_key),
) -> dict:
    """Return the tenant's final prices by ACRISS code (active rule applied).

    Query params (all optional):
      - location_id: restrict to one canonical market (default: all mapped).
      - zone_from / zone_to: inclusive 0-based season range (default: all).
    """
    factory = make_session_factory(app_engine())
    with tenant_context(factory, tenant_id) as session:
        trow = session.execute(text("SELECT name, currency FROM tenants")).fetchone()
        tenant_name = trow.name if trow else ""
        currency = trow.currency if trow else ""
        result = _export_result(session, tenant_id, location_id, zone_from, zone_to)
    return _serialize(result, tenant_name, currency, location_id)
