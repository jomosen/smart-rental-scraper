"""DTOs for the classification service."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VehicleClassificationInput:
    """Input for batch provider classification.

    Carries observed attributes plus price context that helps the LLM
    distinguish within-provider price tiers.

    `transmission` and `fuel_type` are optional hints scraped from the
    provider page — the authoritative values are returned by the LLM as
    ACRISS attribute codes (positions 3 and 4 of the ACRISS code).
    """
    external_code: str | None
    external_name: str | None
    example_models: str
    seats: int | None
    luggage: int | None
    transmission: str | None
    fuel_type: str | None
    representative_price_7d: float | None
    representative_currency: str | None


@dataclass(frozen=True)
class VehicleAttributes:
    """Observed attributes of a vehicle group extracted by a scraper."""
    example_models: str
    seats: int | None
    luggage: int | None
    transmission: str | None   # 'manual' | 'automatic' | None
    fuel_type: str | None      # 'gasoline' | 'diesel' | 'hybrid' | 'electric' | None
    external_code: str | None
    external_name: str | None


@dataclass(frozen=True)
class ClassificationResult:
    """Outcome of an ACRISS classification attempt.

    The four `acriss_*` attributes are None ONLY when no valid code could be
    assigned — i.e. the LLM returned null or an out-of-catalog (hallucinated)
    code. A `pending_review=True` result normally STILL carries a best-guess
    code: pending_review flags low confidence (< 0.85) or a mixed group, not
    the absence of a code. `confidence` is the LLM's self-reported certainty.
    """
    acriss_category: str | None      # ACRISS position 1: vehicle category (E, C, I, …)
    acriss_body_type: str | None     # ACRISS position 2: body type (D, G, M, V, …)
    acriss_transmission: str | None  # ACRISS position 3: transmission (M, A)
    acriss_fuel: str | None          # ACRISS position 4: fuel/drive (R, H, E, …)
    confidence: float
    pending_review: bool
    rationale: str | None = None
    # Set ONLY when the classification never happened — the LLM call itself
    # failed (network, quota, region block). Distinct from a real "could not
    # classify" outcome: consumers must not cache or persist an errored result
    # as a classification. None for every genuine outcome, however uncertain.
    error: str | None = None
