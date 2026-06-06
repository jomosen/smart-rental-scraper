"""Cross-tariff data + pricing-config endpoints.

  GET  /api/cross-tariff            — render from the tenant's SAVED config
                                      (defaults if none). Accepts zone/location_id.
  POST /api/cross-tariff/preview    — render from a config in the body, WITHOUT
                                      persisting (live editing in the front).
  PUT  /api/pricing-config          — validate + persist the tenant's config.

The tenant comes from the session (get_current_tenant); everything runs inside
tenant_context on the RLS engine. Calculation/naming/ordering live in
application/pricing (shared with the Streamlit back-office); this route fetches,
validates, configures and serializes.

Overrides for live editing use the preview POST (not query params) because
category_rules is a nested dict of arbitrary size that does not encode cleanly
in a query string. The preview body is the same shape as the PUT body, so "a
pricing config" has a single schema.

CSRF: the mutating/echo endpoints (PUT, preview) require the header
X-Requested-With: fetch — a cheap defence on top of SameSite=Lax cookies.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
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


# ── Request bodies (Pydantic → automatic 422 on bad enums / negative val) ─────

class RuleBody(BaseModel):
    op: Literal["sub", "add"]
    val: float = Field(ge=0)
    mode: Literal["pct", "abs"]
    floor: Literal["auto", "cost", "none"]
    ceiling: Literal["max", "none"]


class PricingConfigBody(BaseModel):
    base: Literal["min", "med", "avg", "max"]
    round: Literal["0", "0.99", "0.90", "0.50", "1"]
    master_provider_key: Optional[str] = None
    default_rule: RuleBody
    category_rules: dict[str, RuleBody] = Field(default_factory=dict)
    # Only meaningful for preview (ignored by PUT).
    zone: Optional[int] = None
    location_id: Optional[int] = None


def require_fetch_header(x_requested_with: str = Header(default="")) -> None:
    if x_requested_with != "fetch":
        raise HTTPException(status_code=403, detail={"error": "Missing X-Requested-With header"})


# ── Config resolution ────────────────────────────────────────────────────────

def _resolve_saved_config(rule_formula: Optional[dict], active_providers: list[str]) -> dict:
    """Build the working config from the tenant's saved rule, clamped to active providers."""
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


def _cfg_from_body(body: PricingConfigBody, session, active_codes: list[str]) -> dict:
    """Translate a validated body into a working config; DB-level checks raise 422."""
    if body.master_provider_key and body.master_provider_key not in active_codes:
        raise HTTPException(
            status_code=422,
            detail={"error": f"Unknown provider '{body.master_provider_key}'"},
        )
    master = body.master_provider_key or (active_codes[0] if active_codes else None)

    if body.category_rules:
        valid = {
            r[0] for r in session.execute(
                text("SELECT code FROM acriss_codes WHERE active = TRUE")
            ).fetchall()
        }
        unknown = [c for c in body.category_rules if c not in valid]
        if unknown:
            raise HTTPException(
                status_code=422,
                detail={"error": f"Unknown ACRISS code(s): {', '.join(sorted(unknown))}"},
            )

    return {
        "providers": list(active_codes),
        "master": master,
        "base": body.base,
        "round_mode": body.round,
        "global_rule": body.default_rule.model_dump(),
        "category_rules": {k: v.model_dump() for k, v in body.category_rules.items()},
    }


def _formula_from_cfg(cfg: dict) -> dict:
    """Persisted formula_jsonb shape (see docs/DATA_MODEL.md pricing_rules)."""
    return {
        "providers": cfg["providers"],
        "base_aggregation": cfg["base"],
        "master_provider": cfg["master"],
        "rounding": cfg["round_mode"],
        "global_rule": cfg["global_rule"],
        "category_overrides": cfg["category_rules"],
    }


# ── Serialization ────────────────────────────────────────────────────────────

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
            "location": location,
            "locations": locations,
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
                        # Calculation pipeline (drawer) + per-cell flags.
                        "market_base": _money(cell.market_base),
                        "after_rule": _money(cell.after_rule),
                        "clamped": cell.clamped,
                        "clamp_bound": _money(cell.clamp_bound),
                        "rounded": _money(cell.rounded),
                        "round_flip": cell.round_flip,
                        "stale": cell.stale,
                        "inferred": cell.inferred,
                        "partial": cell.partial,
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


def _compute_payload(
    session, tenant_id: uuid.UUID, cfg: dict,
    zone: Optional[int], location_id: Optional[int],
) -> dict:
    """Shared render path for GET and preview. Raises 404 for an unknown location_id."""
    trow = session.execute(text("SELECT name, plan FROM tenants")).fetchone()
    tenant_name = trow.name if trow else ""
    plan = trow.plan if trow else None

    active_providers = fetch_active_providers(session)
    provider_names = {code: name for code, name in active_providers}
    provider_codes = cfg["providers"]
    master_code = cfg["master"]

    canonical_locations = fetch_canonical_locations(session)
    if location_id is not None:
        location = fetch_location(session, location_id)
        if location is None:
            raise HTTPException(status_code=404, detail={"error": "Location not found"})
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


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/api/cross-tariff")
def get_cross_tariff(
    location_id: Optional[int] = Query(default=None),
    zone: Optional[int] = Query(default=None),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
) -> dict:
    factory = make_session_factory(_get_engine())
    with tenant_context(factory, tenant_id) as session:
        active_codes = [code for code, _ in fetch_active_providers(session)]
        active_rule = PricingRuleRepository(session).get_active(tenant_id)
        cfg = _resolve_saved_config(
            active_rule.formula_jsonb if active_rule else None, active_codes
        )
        return _compute_payload(session, tenant_id, cfg, zone, location_id)


@router.post("/api/cross-tariff/preview", dependencies=[Depends(require_fetch_header)])
def preview_cross_tariff(
    body: PricingConfigBody,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
) -> dict:
    factory = make_session_factory(_get_engine())
    with tenant_context(factory, tenant_id) as session:
        active_codes = [code for code, _ in fetch_active_providers(session)]
        cfg = _cfg_from_body(body, session, active_codes)
        return _compute_payload(session, tenant_id, cfg, body.zone, body.location_id)


@router.put("/api/pricing-config", dependencies=[Depends(require_fetch_header)])
def put_pricing_config(
    body: PricingConfigBody,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
) -> dict:
    factory = make_session_factory(_get_engine())
    with tenant_context(factory, tenant_id) as session:
        active_codes = [code for code, _ in fetch_active_providers(session)]
        cfg = _cfg_from_body(body, session, active_codes)
        rule = PricingRuleRepository(session).save(
            tenant_id=tenant_id,
            name="cross-tariff config",
            formula_jsonb=_formula_from_cfg(cfg),
            acriss_code=None,
        )
        # tenant_context commits on exit.
        return {"ok": True, "version": rule.version}
