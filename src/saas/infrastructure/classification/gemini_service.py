"""Gemini-backed implementation of ClassificationService.

Strategy (batch):
  - Primary: Gemini Flash. Fast, cheap.
  - Fallback: Gemini Pro, invoked when ANY vehicle in the batch returns
    confidence < 0.85 (and is not pending_review from an unknown code).
  - If Pro results still below threshold: pending_review.
  - On API error: pending_review with confidence=0.
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
    VehicleClassificationInput,
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

    def classify_provider_batch(
        self,
        provider_code: str,
        vehicles: list[VehicleClassificationInput],
    ) -> list[ClassificationResult]:
        # Step 1: try Flash for the whole batch
        try:
            flash_results = self._call_flash_batch(provider_code, vehicles)
        except Exception as exc:
            logger.warning("Gemini Flash batch call failed: %s", exc)
            return [
                self._pending_review_result(rationale=f"Flash call failed: {exc}")
                for _ in vehicles
            ]

        flash_results = [self._validate_code(r) for r in flash_results]

        needs_pro = any(
            not r.pending_review and r.confidence < _CONFIDENCE_THRESHOLD
            for r in flash_results
        )

        if not needs_pro:
            return flash_results

        # Step 2: at least one below threshold — escalate the whole batch to Pro
        logger.info(
            "Flash batch had low-confidence results for provider %s, escalating to Pro",
            provider_code,
        )
        try:
            pro_results = self._call_pro_batch(provider_code, vehicles)
        except Exception as exc:
            logger.warning("Gemini Pro batch fallback failed: %s", exc)
            return [
                r
                if r.pending_review or r.confidence >= _CONFIDENCE_THRESHOLD
                else ClassificationResult(
                    canonical_type_code=None,
                    confidence=r.confidence,
                    taxonomy_version=self._taxonomy_version,
                    pending_review=True,
                    rationale=(
                        f"Flash confidence {r.confidence:.2f} below threshold; "
                        f"Pro fallback failed: {exc}"
                    ),
                )
                for r in flash_results
            ]

        pro_results = [self._validate_code(r) for r in pro_results]

        final: list[ClassificationResult] = []
        for flash_r, pro_r in zip(flash_results, pro_results):
            if flash_r.pending_review:
                # Flash gave up (unknown code): Pro result irrelevant
                final.append(flash_r)
            elif pro_r.pending_review:
                final.append(pro_r)
            elif pro_r.confidence >= _CONFIDENCE_THRESHOLD:
                final.append(pro_r)
            else:
                final.append(ClassificationResult(
                    canonical_type_code=None,
                    confidence=max(flash_r.confidence, pro_r.confidence),
                    taxonomy_version=self._taxonomy_version,
                    pending_review=True,
                    rationale=(
                        f"Both Flash ({flash_r.confidence:.2f}) and Pro "
                        f"({pro_r.confidence:.2f}) below 0.85 threshold."
                    ),
                ))
        return final

    # ── Private ──────────────────────────────────────────────────────────────

    def _call_flash_batch(
        self, provider_code: str, vehicles: list[VehicleClassificationInput]
    ) -> list[ClassificationResult]:
        return self._call_model_batch(_FLASH_MODEL, provider_code, vehicles)

    def _call_pro_batch(
        self, provider_code: str, vehicles: list[VehicleClassificationInput]
    ) -> list[ClassificationResult]:
        return self._call_model_batch(_PRO_MODEL, provider_code, vehicles)

    def _call_model_batch(
        self,
        model: str,
        provider_code: str,
        vehicles: list[VehicleClassificationInput],
    ) -> list[ClassificationResult]:
        prompt = self._build_batch_prompt(provider_code, vehicles)
        response = self._client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
        raw: list = json.loads(response.text)
        results: list[ClassificationResult] = []
        for item in raw:
            code = item.get("canonical_type_code") or None
            confidence = float(item.get("confidence", 0.0))
            rationale = item.get("rationale")
            if code is None:
                results.append(ClassificationResult(
                    canonical_type_code=None,
                    confidence=confidence,
                    taxonomy_version=self._taxonomy_version,
                    pending_review=True,
                    rationale=rationale,
                ))
            else:
                results.append(ClassificationResult(
                    canonical_type_code=code,
                    confidence=confidence,
                    taxonomy_version=self._taxonomy_version,
                    pending_review=False,
                    rationale=rationale,
                ))
        return results

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

    def _build_batch_prompt(
        self, provider_code: str, vehicles: list[VehicleClassificationInput]
    ) -> str:
        canonical_block = "\n\n".join(
            self._render_canonical(ct) for ct in self._canonical_types
        )
        vehicles_block = "\n\n".join(
            f"### Vehicle {i + 1}\n{self._render_batch_vehicle(v)}"
            for i, v in enumerate(vehicles)
        )
        return (
            "You are a vehicle rental classification specialist.\n\n"
            f"Provider code: {provider_code}\n\n"
            "Classify each vehicle below into ONE of the canonical categories.\n"
            "Use the price information to help distinguish within-provider price tiers —\n"
            "the same provider may have multiple groups mapping to the same category.\n"
            "If no category fits well, respond with confidence < 0.85 and "
            "canonical_type_code = null.\n\n"
            f"## CANONICAL CATEGORIES\n\n{canonical_block}\n\n"
            f"## VEHICLES TO CLASSIFY\n\n{vehicles_block}\n\n"
            "## OUTPUT\n\n"
            "Respond in JSON as an array with one object per vehicle, in the same order:\n"
            "  [{\"canonical_type_code\": ..., \"confidence\": ..., \"rationale\": ...}, ...]\n\n"
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

    def _render_batch_vehicle(self, v: VehicleClassificationInput) -> str:
        lines = [f"Models: {v.example_models}"]
        if v.seats is not None:
            lines.append(f"Seats: {v.seats}")
        if v.luggage is not None:
            lines.append(f"Luggage capacity: {v.luggage}")
        if v.transmission:
            lines.append(f"Transmission: {v.transmission}")
        if v.fuel_type:
            lines.append(f"Fuel type: {v.fuel_type}")
        if v.external_code:
            lines.append(f"Provider's internal code: {v.external_code}")
        if v.external_name:
            lines.append(f"Provider's display name: {v.external_name}")
        if v.representative_price_7d is not None:
            currency = v.representative_currency or ""
            price_line = f"Representative 7-day price: {v.representative_price_7d:.2f}"
            if currency:
                price_line += f" {currency}"
            lines.append(price_line)
        return "\n".join(lines)
