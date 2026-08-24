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
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

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


def _compose_code(r: ClassificationResult) -> Optional[str]:
    parts = [r.acriss_category, r.acriss_body_type, r.acriss_transmission, r.acriss_fuel]
    return "".join(parts) if all(parts) else None


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
                    seats=None,
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
            confidence = result.confidence
            pending = result.pending_review
            cache.upsert(norm, version, code, confidence, pending)
            from_cache = False

        description: Optional[str] = None
        example_models: list[str] = []
        if code:
            ac = AcrissCodeRepository(session).get_by_code(code)
            if ac is not None:
                description = ac.display_name
                example_models = list(ac.examples or [])

    return {
        "model": model,
        "acriss_code": code,
        "description": description,
        "example_models": example_models,
        "confidence": round(confidence, 3),
        "pending_review": pending,
        "cached": from_cache,
    }
