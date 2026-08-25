"""Ad-hoc classification API (v1) — free-text model → ACRISS code.

GET /api/v1/classify?model=Peugeot%20208%20Manual
  → { acriss_code, description, example_models, confidence, pending_review, cached }

Authenticated by the per-tenant API key (Bearer), same as /api/v1/prices.
Read-through cache (model_classifications): the LLM is hit only on a cache miss;
the cache key includes a classifier_version (hash of acriss_codes.yaml + prompt
version + active Gemini models) so a catalog/prompt/model change invalidates
stale entries.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from src.saas.application.classification.acriss_engine.dictionaries import (
    ALIASES_PATH,
    MODELS_PATH,
    OVERRIDES_PATH,
)
from src.saas.application.classification.acriss_loader import load_acriss_specs
from src.saas.application.classification.dtos import (
    ClassificationResult,
    VehicleClassificationInput,
)
from src.saas.application.classification.service import ClassificationService
from src.saas.infrastructure.classification.engine_service import (
    AcrissEngineClassificationService,
)
from src.saas.infrastructure.classification.gemini_service import flash_model
from src.saas.infrastructure.classification.semantic_resolver import (
    RESOLVER_PROMPT_VERSION,
    SemanticModelResolver,
)
from src.saas.infrastructure.persistence.engine import app_engine
from src.saas.infrastructure.persistence.repositories.acriss_code_repository import (
    AcrissCodeRepository,
)
from src.saas.infrastructure.persistence.repositories.model_classification import (
    ModelClassificationRepository,
)
from src.saas.infrastructure.persistence.session import make_session_factory, tenant_context

from ..dependencies import get_tenant_from_api_key

router = APIRouter()

_YAML_PATH = Path(__file__).resolve().parents[5] / "acriss_codes.yaml"

_classifier: Optional[ClassificationService] = None
_version: Optional[str] = None


def get_classifier() -> ClassificationService:
    """Lazily-built, process-cached classifier (FastAPI dependency; override in tests).

    Engine v2 (deterministic). Gemini only resolves unknown models
    semantically; unknowns are queued in acriss_review_queue.
    """
    global _classifier
    if _classifier is None:
        _classifier = AcrissEngineClassificationService(
            materialized_codes={s.code for s in load_acriss_specs(_YAML_PATH)},
            resolver=SemanticModelResolver(),
            session_factory=make_session_factory(app_engine()),
        )
    return _classifier


def _digest(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:12]


def _classifier_version() -> str:
    """Cache key version for model_classifications.

    Anything that changes a classification rotates the version: the
    materialized catalog, the three engine data files, the engine marker,
    the resolver prompt, and the resolver's Gemini model.
    """
    global _version
    if _version is None:
        _version = ":".join([
            _digest(_YAML_PATH),
            _digest(MODELS_PATH),
            _digest(ALIASES_PATH),
            _digest(OVERRIDES_PATH),
            "engine2",
            RESOLVER_PROMPT_VERSION,
            flash_model(),
        ])
    return _version


def _normalize(model: str) -> str:
    """Lowercase + collapse whitespace — the cache key."""
    return " ".join(model.split()).lower()


_SEATS_IN_TEXT = re.compile(
    r"\b(\d{1,2})\s*(?:plazas?|pax|seats?|asientos|places|posti|p)\b",
    re.IGNORECASE,
)


def _seats_from_text(model: str) -> Optional[int]:
    """Seat count declared inside the free text ('9 plazas', '9PAX', '7p').

    Passenger-van coding hangs off capacity (§10: 9→LV, 8→PV, 7→SV), so a
    declared count must reach the engine as `seats`, not stay buried in the
    name. Bounded to plausible rental capacities to avoid grabbing engine
    displacements or model digits.
    """
    m = _SEATS_IN_TEXT.search(model)
    if not m:
        return None
    n = int(m.group(1))
    return n if 2 <= n <= 20 else None


def _compose_code(r: ClassificationResult) -> Optional[str]:
    parts = [r.acriss_category, r.acriss_body_type, r.acriss_transmission, r.acriss_fuel]
    return "".join(parts) if all(parts) else None


def _full_code(r: ClassificationResult) -> Optional[str]:
    """The engine's exact letters — even when the catalog does not materialize
    them (detail carries the original code through trunk collapses)."""
    d = r.detail or {}
    return d.get("unmaterialized_code") or _compose_code(r)


def _group_code(full: Optional[str]) -> Optional[str]:
    """Commercial group: first three letters + wildcard. BEV exception: an
    electric (fuel E/C) keeps its fourth letter — different rental policy,
    range, deposit and demand make it its own commercial category."""
    if not full or len(full) != 4:
        return None
    if full[3] in ("E", "C"):
        return full
    return full[:3] + "*"


@router.get("/api/v1/classify")
def classify(
    model: str = Query(default="", description="Free-text vehicle model, e.g. 'Peugeot 208 Manual'"),
    tenant_id: uuid.UUID = Depends(get_tenant_from_api_key),
    classifier: ClassificationService = Depends(get_classifier),
) -> dict:
    norm = _normalize(model)
    if not norm:
        raise HTTPException(status_code=400, detail={"error": "model is required"})

    version = _classifier_version()
    factory = make_session_factory(app_engine())
    with tenant_context(factory, tenant_id) as session:
        cache = ModelClassificationRepository(session)
        row = cache.get(norm, version)
        if row is not None:
            cache.touch(norm, version)
            code = row.acriss_code
            full = row.acriss_full or row.acriss_code
            confidence = float(row.confidence)
            pending = row.pending_review
            from_cache = True
        else:
            result = classifier.classify_provider_batch(
                "adhoc",
                [VehicleClassificationInput(
                    external_code=None,
                    external_name=model,
                    example_models=model,
                    seats=_seats_from_text(model),
                    luggage=None,
                    transmission=None,
                    fuel_type=None,
                    representative_price_7d=None,
                    representative_currency=None,
                )],
            )[0]
            # Transport-level failure (network, quota, region block): the LLM
            # was never reached, so there is nothing to cache — persisting it
            # would poison the cache and keep serving null after the outage.
            # Surface the upstream message so the operator can see WHY.
            if result.error:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "error": "classification_unavailable",
                        "message": result.error,
                    },
                )
            code = _compose_code(result)
            full = _full_code(result)
            confidence = result.confidence
            pending = result.pending_review
            cache.upsert(norm, version, code, confidence, pending, acriss_full=full)
            from_cache = False

        # Market-aware recommendation (applied LIVE, cache hits included, so
        # it tracks the market instead of freezing with the cached row): a
        # specific-fuel code with no active market groups is commercially
        # empty — providers there don't split by that fuel — so the mapping
        # target falls back to its R trunk. Where the market DOES separate
        # (hybrids today), the specific stays. BEVs (E/C) never collapse.
        # acriss_full keeps the honest letters either way.
        if code and len(code) == 4 and code[3] in ("D", "H", "I"):
            has_market = session.execute(
                text(
                    "SELECT EXISTS ("
                    "  SELECT 1 FROM provider_vehicle_categories pvc"
                    "  JOIN providers p ON p.id = pvc.provider_id"
                    "  WHERE pvc.active AND p.status = 'active'"
                    "    AND pvc.acriss_code = :c)"
                ),
                {"c": code},
            ).scalar()
            if not has_market:
                trunk_row = AcrissCodeRepository(session).get_by_code(code[:3] + "R")
                if trunk_row is not None and trunk_row.active:
                    code = trunk_row.code

        description: Optional[str] = None
        example_models: list[str] = []
        if code:
            ac = AcrissCodeRepository(session).get_by_code(code)
            if ac is not None:
                description = ac.display_name
                example_models = list(ac.examples or [])

    return {
        "model": model,
        # Recommended, materialized code — what a mapping should target.
        "acriss_code": code,
        # The engine's exact letters, possibly unmaterialized (IGAV for an
        # explicit-petrol query). Informational: not necessarily mappable.
        "acriss_full": full,
        # Commercial group (category+body+transmission, fuel wildcarded).
        # BEVs keep their fourth letter — electric is its own group.
        "acriss_group": _group_code(full),
        "description": description,
        "example_models": example_models,
        "confidence": round(confidence, 3),
        "pending_review": pending,
        "cached": from_cache,
    }
