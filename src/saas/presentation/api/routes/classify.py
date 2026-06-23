"""Ad-hoc classification API (v1) — free-text model → ACRISS code.

GET /api/v1/classify?model=Peugeot%20208%20Manual
  → { acriss_code, description, example_models, confidence, pending_review, cached }

Authenticated by the per-tenant API key (Bearer), same as /api/v1/prices.
Read-through cache (model_classifications): the LLM is hit only on a cache miss;
the cache key includes a classifier_version (hash of acriss_codes.yaml + the
prompt version) so a catalog/prompt change invalidates stale entries.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.saas.application.classification.acriss_loader import load_acriss_specs
from src.saas.application.classification.dtos import (
    ClassificationResult,
    VehicleClassificationInput,
)
from src.saas.application.classification.service import ClassificationService
from src.saas.infrastructure.classification.gemini_service import (
    PROMPT_VERSION,
    GeminiClassificationService,
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
    """Lazily-built, process-cached classifier (FastAPI dependency; override in tests)."""
    global _classifier
    if _classifier is None:
        _classifier = GeminiClassificationService(acriss_types=load_acriss_specs(_YAML_PATH))
    return _classifier


def _classifier_version() -> str:
    global _version
    if _version is None:
        digest = hashlib.sha1(_YAML_PATH.read_bytes()).hexdigest()[:12]
        _version = f"{digest}:{PROMPT_VERSION}"
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
