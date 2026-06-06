"""GET /api/cross-tariff — read-only data for the price-radar grid.

The tenant is resolved by `get_current_tenant` (Fase 1: DEV_TENANT_ID env var).
The request runs inside `tenant_context` on the RLS-enforcing app engine, so
tenant-scoped tables (tenants, pricing_rules) are isolated; the global catalog
(providers, prices, zones, acriss_codes) is read in the same session.

Calculation, naming and ordering are NOT done here — they live in
application/pricing (cross_tariff_calc + cross_tariff_assembler), shared with
the Streamlit back-office. This route only fetches, configures and serializes.

Ephemeral overrides (Fase 2): the query params base / round / master /
rule_op / rule_val / rule_mode / rule_floor / rule_ceiling override the tenant's
resolved defaults for this request only. Nothing is persisted — they are simply
fed to the assembler, keeping the calculation in a single place.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Engine, text

from src.saas.application.pricing.cross_tariff_assembler import (
    CrossTariffPayload,
    assemble_cross_tariff,
)
from src.saas.infrastructure.persistence.engine import app_engine
from src.saas.infrastructure.persistence.read.cross_tariff_read import (
    DURATIONS,
    fetch_active_providers,
    fetch_canonical_locations,
    fetch_catalog_examples,
    fetch_cross_tariff_dataframe,
    fetch_data_updated_at,
    fetch_location,
)
from src.saas.infrastructure.persistence.repositories import PricingRuleRepository
from src.saas.infrastructure.persistence.session import make_session_factory, tenant_context

from ..dependencies import get_current_tenant

router = APIRouter()

# Defaults mirror the Streamlit cross-tariff view, so an un-configured tenant
# produces the same numbers as the back-office grid.
_DEFAULT_RULE = {"op": "sub", "val": 1.0, "mode": "pct", "floor": "auto", "ceiling": "max"}
_DEFAULT_BASE = "min"
_DEFAULT_ROUND = "0"

# Lazy engine singleton — avoids building a pool per request.
_engine: Engine | None = None


def _get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = app_engine()
    return _engine


def _money(x: Optional[Decimal]) -> Optional[str]:
    if x is None:
        return None
    return str(Decimal(x).quantize(Decimal("0.01")))


def _resolve_config(rule_formula: Optional[dict], active_providers: list[str]) -> dict:
    """Merge the tenant's saved pricing rule with defaults, clamped to active providers."""
    f = rule_formula or {}
    providers = [p for p in (f.get("providers") or active_providers) if p in active_providers]
    if not providers:
        providers = active_providers
    master = f.get("master_provider")
    if master not in providers:
        master = providers[0] if providers else None
    return {
        "providers": providers,
        "master": master,
        "base": f.get("base_aggregation", _DEFAULT_BASE),
        "round_mode": f.get("rounding", _DEFAULT_ROUND),
        "global_rule": dict(f.get("global_rule", _DEFAULT_RULE)),
        "category_rules": f.get("category_overrides", {}),
    }


def _apply_overrides(cfg: dict, *, base, round_mode, master,
                     rule_op, rule_val, rule_mode, rule_floor, rule_ceiling) -> None:
    """Apply ephemeral query-param overrides onto the resolved config (in place)."""
    if base:
        cfg["base"] = base
    if round_mode is not None:
        cfg["round_mode"] = round_mode
    if master and master in cfg["providers"]:
        cfg["master"] = master
    gr = dict(cfg["global_rule"])
    if rule_op:
        gr["op"] = rule_op
    if rule_val is not None:
        gr["val"] = rule_val
    if rule_mode:
        gr["mode"] = rule_mode
    if rule_floor:
        gr["floor"] = rule_floor
    if rule_ceiling:
        gr["ceiling"] = rule_ceiling
    cfg["global_rule"] = gr


def _serialize(
    payload: CrossTariffPayload,
    *,
    tenant_name: str,
    plan: Optional[str],
    location: Optional[dict],
    locations: list[dict],
    data_updated_at,
    base: str,
    round_mode: str,
    rules: dict,
) -> dict:
    return {
        "meta": {
            "tenant_name": tenant_name,
            "plan": plan,
            "location": location,        # selected canonical market {id, code, name} | None
            "locations": locations,      # selectable canonical markets (those with data)
            "zone": {
                "index": payload.zone.index,
                "total": payload.zone.total,
                "date_from": payload.zone.date_from.isoformat() if payload.zone.date_from else None,
                "date_to": payload.zone.date_to.isoformat() if payload.zone.date_to else None,
            },
            "master_rep_date": payload.master_rep_date.isoformat() if payload.master_rep_date else None,
            "durations": payload.durations,
            "providers": [
                {"key": p.key, "name": p.name, "color": p.color, "is_master": p.is_master}
                for p in payload.providers
            ],
            "data_updated_at": data_updated_at.isoformat() if data_updated_at else None,
            "base": base,
            "round": round_mode,
            "rules": rules,
        },
        "categories": [
            {
                "acriss_code": c.acriss_code,
                "view_label": c.view_label,
                "catalog_examples": c.catalog_examples,
                "flags": c.flags,
                "cells": [
                    {
                        "duration": cell.duration,
                        "recommended_total": _money(cell.recommended_total),
                        "recommended_per_day": _money(cell.recommended_per_day),
                        "market_min": _money(cell.market_min),
                        "market_max": _money(cell.market_max),
                        "empty": cell.empty,
                    }
                    for cell in c.cells
                ],
                "providers": [
                    {
                        "provider_key": pr.provider_key,
                        "models": pr.models,
                        "transmission": pr.transmission,
                        "inferred": pr.inferred,
                        "cells": [
                            {
                                "duration": pc.duration,
                                "total": _money(pc.total),
                                "per_day": _money(pc.per_day),
                                "is_base": pc.is_base,
                                "anomaly": pc.anomaly,
                                "missing": pc.missing,
                            }
                            for pc in pr.cells
                        ],
                    }
                    for pr in c.providers
                ],
            }
            for c in payload.categories
        ],
    }


@router.get("/api/cross-tariff")
def get_cross_tariff(
    location_id: Optional[int] = Query(default=None),
    zone: Optional[int] = Query(default=None),
    base: Optional[str] = Query(default=None),
    round_mode: Optional[str] = Query(default=None, alias="round"),
    master: Optional[str] = Query(default=None),
    rule_op: Optional[str] = Query(default=None),
    rule_val: Optional[float] = Query(default=None),
    rule_mode: Optional[str] = Query(default=None),
    rule_floor: Optional[str] = Query(default=None),
    rule_ceiling: Optional[str] = Query(default=None),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
) -> dict:
    factory = make_session_factory(_get_engine())
    with tenant_context(factory, tenant_id) as session:
        # Tenant-scoped reads (RLS-filtered to this tenant).
        trow = session.execute(text("SELECT name, plan FROM tenants")).fetchone()
        tenant_name = trow.name if trow else ""
        plan = trow.plan if trow else None
        active_rule = PricingRuleRepository(session).get_active(tenant_id)
        rule_formula = active_rule.formula_jsonb if active_rule else None

        # Global catalog reads (not RLS-scoped) in the same session.
        active_providers = fetch_active_providers(session)
        provider_names = {code: name for code, name in active_providers}
        active_codes = [code for code, _ in active_providers]

        cfg = _resolve_config(rule_formula, active_codes)
        _apply_overrides(
            cfg, base=base, round_mode=round_mode, master=master,
            rule_op=rule_op, rule_val=rule_val, rule_mode=rule_mode,
            rule_floor=rule_floor, rule_ceiling=rule_ceiling,
        )
        provider_codes = cfg["providers"]
        master_code = cfg["master"]

        # Canonical market locations: the selectable markets (those with data) and
        # the one in effect for this request. An explicit location_id that does not
        # exist in the catalog is a 404 (not silent). A valid market with no mapped
        # offices simply yields an empty grid.
        canonical_locations = fetch_canonical_locations(session)
        if location_id is not None:
            location = fetch_location(session, location_id)
            if location is None:
                raise HTTPException(
                    status_code=404, detail={"error": "Location not found"}
                )
            selected_location_id = location_id
        else:
            selected_location_id = canonical_locations[0]["id"] if canonical_locations else None
            location = fetch_location(session, selected_location_id) if selected_location_id else None

        df = fetch_cross_tariff_dataframe(
            session, tuple(provider_codes), DURATIONS, True, location_id=selected_location_id
        )
        examples = fetch_catalog_examples(session)
        updated_at = fetch_data_updated_at(session, tuple(provider_codes))

        payload = assemble_cross_tariff(
            df=df,
            providers=provider_codes,
            master=master_code,
            base=cfg["base"],
            round_mode=cfg["round_mode"],
            global_rule=cfg["global_rule"],
            category_rules=cfg["category_rules"],
            durations=list(DURATIONS),
            examples=examples,
            zone_index=zone,
        )

    # Real provider display names in meta (assembler only had the keys).
    for pm in payload.providers:
        pm.name = provider_names.get(pm.key, pm.key)

    rules_out = {"_default": cfg["global_rule"], **cfg["category_rules"]}
    return _serialize(
        payload,
        tenant_name=tenant_name,
        plan=plan,
        location=location,
        locations=canonical_locations,
        data_updated_at=updated_at,
        base=cfg["base"],
        round_mode=cfg["round_mode"],
        rules=rules_out,
    )
