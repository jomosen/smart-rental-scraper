"""DTOs for the classification service."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VehicleAttributes:
    """Observed attributes of a vehicle group extracted by a scraper.

    Used as input to ClassificationService.classify().
    """
    example_models: str
    seats: int | None
    luggage: int | None
    transmission: str | None   # 'manual' | 'automatic' | None
    fuel_type: str | None      # 'gasoline' | 'diesel' | 'hybrid' | 'electric' | None
    external_code: str | None  # Provider's group code if exposed, else None
    external_name: str | None  # Provider's display name if exposed, else None


@dataclass(frozen=True)
class ClassificationResult:
    """Outcome of a classification attempt.

    `canonical_type_code` is None when the LLM (after fallback) could
    not confidently classify. In that case, `pending_review` is True
    and `confidence` is the highest confidence achieved (still below
    threshold).

    `taxonomy_version` is the version used at classification time —
    persisted alongside the result so future taxonomy changes can
    selectively invalidate stale classifications.
    """
    canonical_type_code: str | None
    confidence: float
    taxonomy_version: int
    pending_review: bool
    rationale: str | None = None
