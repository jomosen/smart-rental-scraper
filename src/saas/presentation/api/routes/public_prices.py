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
from src.saas.infrastructure.persistence.read.catalog_read import split_models
from src.saas.infrastructure.persistence.read.cross_tariff_read import (
    fetch_active_providers,
    fetch_catalog_examples,
)
from src.saas.infrastructure.persistence.session import make_session_factory, tenant_context

from ..dependencies import get_tenant_from_api_key
# Reuse the dashboard's priced-rows builder and the canonical duration set so the
# API and the dashboard/export never drift.
from .cross_tariff import DURATIONS, _export_result

router = APIRouter()


def _money(value: Optional[Decimal]) -> Optional[float]:
    return float(value) if value is not None else None


def _serialize(
    result,
    tenant_name: str,
    currency: str,
    location_id: Optional[int],
    examples: dict[str, list[str]],
    providers: list[tuple[str, str]],
) -> dict:
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
                # Curated example vehicle models for this category (catalog-level,
                # constant across zones). [] when the catalog lists none.
                "example_models": list(examples.get(r.acriss_code, [])),
                "zone": {
                    "index": r.zone_index,
                    "date_from": r.zone_desde.isoformat() if r.zone_desde else None,
                    "date_to": r.zone_hasta.isoformat() if r.zone_hasta else None,
                },
                "prices_per_day": {},
                "prices_total": {},
                # Provenance per duration: which provider set the recommended
                # price (base_provider), its actual total before the tenant's
                # rule (base_total), and every provider's total for that cell.
                "provenance": {},
            }
            groups[key] = g
            order.append(key)
        dur = str(r.duracion_dias)
        g["prices_per_day"][dur] = _money(r.recomendado_per_day)
        g["prices_total"][dur] = _money(r.recomendado_total)
        models = r.provider_models or {}
        codes = r.provider_external_codes or {}
        provider_totals = {
            code: _money(total)
            for code, total in (r.provider_prices or {}).items()
            if total is not None
        }
        # Provenance per duration: which provider set the recommended price
        # (base_provider/base_total) and, per provider, its total + the actual
        # model it lists for this category.
        #
        # external_code is the provider's own code for the group behind `total`.
        # When a provider splits the category into several groups, `total` is the
        # cheapest and external_code names that group, while `model` still lists
        # every group's models — so the two can describe different tiers.
        #
        # `groups` is the un-collapsed view: one entry per group of the provider
        # with a price at this duration, cheapest first, each with the identity
        # to reference it (`group_key`, same rule as /api/v1/provider-groups)
        # and its own price. This is what lets a consumer read the price of a
        # specific group even when it is not the provider's cheapest.
        group_lists = r.provider_groups or {}
        g["provenance"][dur] = {
            "base_provider": r.base_provider,
            "base_total": provider_totals.get(r.base_provider) if r.base_provider else None,
            "by_provider": {
                code: {
                    "total": total,
                    "model": (models.get(code) or None),
                    "external_code": codes.get(code),
                    "groups": [
                        {
                            "group_key": grp.group_key,
                            "models": split_models(grp.models),
                            "total": _money(grp.total),
                            "per_day": _money(grp.per_day),
                            "is_base": grp.is_base,
                        }
                        for grp in group_lists.get(code, [])
                    ],
                }
                for code, total in provider_totals.items()
            },
        }

    return {
        "tenant": tenant_name,
        "currency": currency,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "location_id": location_id,
        "durations": list(DURATIONS),
        # Providers in the radar (the codes used in each entry's provenance).
        "providers": [{"code": code, "name": name} for code, name in providers],
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
        examples = fetch_catalog_examples(session)
        providers = fetch_active_providers(session)
    return _serialize(result, tenant_name, currency, location_id, examples, providers)
