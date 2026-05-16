"""Gemini-backed implementation of ClassificationService.

Strategy:
  - Primary: Gemini Flash. Fast, cheap.
  - Fallback: Gemini Pro, invoked only when Flash returns
    confidence < 0.85.
  - If both stay below threshold: pending_review.
  - On API error (timeout, network, rate limit): pending_review
    with confidence=0.

The prompt embeds all canonical types with their descriptions,
criteria, and examples (Option C of the design discussion).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

from google import genai
from google.genai import types

from src.saas.application.classification.dtos import (
    ClassificationResult,
    VehicleAttributes,
)
from src.saas.application.classification.service import ClassificationService

_CONFIDENCE_THRESHOLD = 0.85
_FLASH_MODEL = "gemini-2.5-flash"
_PRO_MODEL = "gemini-2.5-pro"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CanonicalTypeSpec:
    """A canonical type as the LLM sees it: code, description, criteria,
    examples. Built from the YAML and from the DB rows."""
    code: str
    name: str
    description: str
    criteria: list[str]
    examples: list[str]


class GeminiClassificationService(ClassificationService):

    def __init__(
        self,
        canonical_types: list[CanonicalTypeSpec],
        taxonomy_version: int,
        api_key: str | None = None,
    ) -> None:
        if not canonical_types:
            raise ValueError("canonical_types cannot be empty")
        self._canonical_types = canonical_types
        self._known_codes = {ct.code for ct in canonical_types}
        self._taxonomy_version = taxonomy_version
        self._api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self._api_key:
            raise ValueError(
                "GEMINI_API_KEY not set. Configure it in .env or pass "
                "explicitly to the service constructor."
            )
        self._init_clients()

    def _init_clients(self) -> None:
        self._client = genai.Client(api_key=self._api_key)

    def classify(self, attributes: VehicleAttributes) -> ClassificationResult:
        # Step 1: try Flash
        try:
            flash_result = self._call_flash(attributes)
        except Exception as exc:
            logger.warning(
                "Gemini Flash call failed for vehicle %r: %s",
                attributes.example_models,
                exc,
            )
            return self._pending_review_result(rationale=f"Flash call failed: {exc}")

        flash_result = self._validate_code(flash_result)
        if flash_result.pending_review:
            # Either LLM returned unknown code, or LLM explicitly said no category fits
            # and confidence is below threshold. Return immediately — Pro won't help
            # when Flash is hallucinating codes.
            return flash_result

        if flash_result.confidence >= _CONFIDENCE_THRESHOLD:
            return flash_result

        # Step 2: Flash confidence too low — escalate to Pro
        logger.info(
            "Flash confidence %.2f below threshold for %r, escalating to Pro",
            flash_result.confidence,
            attributes.example_models,
        )
        try:
            pro_result = self._call_pro(attributes)
        except Exception as exc:
            logger.warning(
                "Gemini Pro fallback failed for vehicle %r: %s",
                attributes.example_models,
                exc,
            )
            return ClassificationResult(
                canonical_type_code=None,
                confidence=flash_result.confidence,
                taxonomy_version=self._taxonomy_version,
                pending_review=True,
                rationale=(
                    f"Flash confidence {flash_result.confidence:.2f} below "
                    f"threshold; Pro fallback failed: {exc}"
                ),
            )

        pro_result = self._validate_code(pro_result)
        if pro_result.pending_review:
            return pro_result

        if pro_result.confidence >= _CONFIDENCE_THRESHOLD:
            return pro_result

        # Step 3: both below threshold
        logger.info(
            "Both Flash (%.2f) and Pro (%.2f) below threshold for %r",
            flash_result.confidence,
            pro_result.confidence,
            attributes.example_models,
        )
        return ClassificationResult(
            canonical_type_code=None,
            confidence=max(flash_result.confidence, pro_result.confidence),
            taxonomy_version=self._taxonomy_version,
            pending_review=True,
            rationale=(
                f"Both Flash ({flash_result.confidence:.2f}) and Pro "
                f"({pro_result.confidence:.2f}) below 0.85 threshold."
            ),
        )

    # ── Private ──────────────────────────────────────────────────────────────

    def _call_flash(self, attributes: VehicleAttributes) -> ClassificationResult:
        return self._call_model(_FLASH_MODEL, attributes)

    def _call_pro(self, attributes: VehicleAttributes) -> ClassificationResult:
        return self._call_model(_PRO_MODEL, attributes)

    def _call_model(
        self, model: str, attributes: VehicleAttributes
    ) -> ClassificationResult:
        prompt = self._build_prompt(attributes)
        response = self._client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
        raw: dict = json.loads(response.text)
        canonical_code = raw.get("canonical_type_code") or None
        confidence = float(raw.get("confidence", 0.0))
        rationale = raw.get("rationale")

        if canonical_code is None:
            return ClassificationResult(
                canonical_type_code=None,
                confidence=confidence,
                taxonomy_version=self._taxonomy_version,
                pending_review=True,
                rationale=rationale,
            )
        return ClassificationResult(
            canonical_type_code=canonical_code,
            confidence=confidence,
            taxonomy_version=self._taxonomy_version,
            pending_review=False,
            rationale=rationale,
        )

    def _validate_code(self, result: ClassificationResult) -> ClassificationResult:
        """If the LLM returned a code not in our taxonomy, treat as pending_review."""
        if (
            result.canonical_type_code is not None
            and result.canonical_type_code not in self._known_codes
        ):
            logger.warning(
                "LLM returned unknown canonical code '%s' — treating as pending_review",
                result.canonical_type_code,
            )
            return ClassificationResult(
                canonical_type_code=None,
                confidence=0.0,
                taxonomy_version=self._taxonomy_version,
                pending_review=True,
                rationale=(
                    f"LLM returned unknown canonical code "
                    f"'{result.canonical_type_code}'"
                ),
            )
        return result

    def _pending_review_result(
        self, rationale: str | None = None
    ) -> ClassificationResult:
        return ClassificationResult(
            canonical_type_code=None,
            confidence=0.0,
            taxonomy_version=self._taxonomy_version,
            pending_review=True,
            rationale=rationale,
        )

    def _build_prompt(self, attributes: VehicleAttributes) -> str:
        canonical_block = "\n\n".join(
            self._render_canonical(ct) for ct in self._canonical_types
        )
        vehicle_block = self._render_vehicle(attributes)
        return (
            "You are a vehicle rental classification specialist.\n\n"
            "Classify the vehicle below into ONE of the canonical categories\n"
            "listed. Choose the category that best fits based on the criteria.\n\n"
            "If no category fits well, respond with confidence < 0.85 and\n"
            "canonical_type_code = null.\n\n"
            f"## CANONICAL CATEGORIES\n\n{canonical_block}\n\n"
            f"## VEHICLE TO CLASSIFY\n\n{vehicle_block}\n\n"
            "## OUTPUT\n\n"
            "Respond in JSON with exactly these fields:\n"
            "  - canonical_type_code: string (one of the category codes above) or null\n"
            "  - confidence: float between 0.0 and 1.0\n"
            "  - rationale: short explanation of the choice\n\n"
            "JSON only. No prose."
        )

    def _render_canonical(self, ct: CanonicalTypeSpec) -> str:
        criteria_block = "\n".join(f"  - {c}" for c in ct.criteria)
        examples_block = ", ".join(ct.examples)
        return (
            f"### {ct.code} ({ct.name})\n"
            f"{ct.description}\n"
            f"Criteria:\n{criteria_block}\n"
            f"Examples: {examples_block}"
        )

    def _render_vehicle(self, attrs: VehicleAttributes) -> str:
        lines = [f"Models: {attrs.example_models}"]
        if attrs.seats is not None:
            lines.append(f"Seats: {attrs.seats}")
        if attrs.luggage is not None:
            lines.append(f"Luggage capacity: {attrs.luggage}")
        if attrs.transmission:
            lines.append(f"Transmission: {attrs.transmission}")
        if attrs.fuel_type:
            lines.append(f"Fuel type: {attrs.fuel_type}")
        if attrs.external_code:
            lines.append(f"Provider's internal code: {attrs.external_code}")
        if attrs.external_name:
            lines.append(f"Provider's display name: {attrs.external_name}")
        return "\n".join(lines)
