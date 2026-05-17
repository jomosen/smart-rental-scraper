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
class AcrissCodeSpec:
    """An ACRISS code as the LLM sees it: 4-char code, description, criteria, examples."""
    code: str           # 4-char ACRISS code, e.g. "EDMR"
    display_name: str
    description: str
    criteria: list[str]
    examples: list[str]

    @property
    def category(self) -> str:
        return self.code[0]

    @property
    def body_type(self) -> str:
        return self.code[1]

    @property
    def transmission(self) -> str:
        return self.code[2]

    @property
    def fuel(self) -> str:
        return self.code[3]


class GeminiClassificationService(ClassificationService):

    def __init__(
        self,
        acriss_types: list[AcrissCodeSpec],
        api_key: str | None = None,
    ) -> None:
        if not acriss_types:
            raise ValueError("acriss_types cannot be empty")
        self._acriss_types = acriss_types
        self._known_codes = {spec.code for spec in acriss_types}
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
                    acriss_category=None,
                    acriss_body_type=None,
                    acriss_transmission=None,
                    acriss_fuel=None,
                    confidence=r.confidence,
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
                final.append(flash_r)
            elif pro_r.pending_review:
                final.append(pro_r)
            elif pro_r.confidence >= _CONFIDENCE_THRESHOLD:
                final.append(pro_r)
            else:
                final.append(ClassificationResult(
                    acriss_category=None,
                    acriss_body_type=None,
                    acriss_transmission=None,
                    acriss_fuel=None,
                    confidence=max(flash_r.confidence, pro_r.confidence),
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
            cat = item.get("acriss_category") or None
            body = item.get("acriss_body_type") or None
            trans = item.get("acriss_transmission") or None
            fuel = item.get("acriss_fuel") or None
            confidence = float(item.get("confidence", 0.0))
            rationale = item.get("rationale")

            all_set = all(x is not None for x in (cat, body, trans, fuel))
            results.append(ClassificationResult(
                acriss_category=cat if all_set else None,
                acriss_body_type=body if all_set else None,
                acriss_transmission=trans if all_set else None,
                acriss_fuel=fuel if all_set else None,
                confidence=confidence,
                pending_review=not all_set,
                rationale=rationale,
            ))
        return results

    def _validate_code(self, result: ClassificationResult) -> ClassificationResult:
        """If the LLM returned a code not in our known ACRISS set, treat as pending_review."""
        if result.pending_review:
            return result
        code = (
            (result.acriss_category or "")
            + (result.acriss_body_type or "")
            + (result.acriss_transmission or "")
            + (result.acriss_fuel or "")
        )
        if code not in self._known_codes:
            logger.warning(
                "LLM returned unknown ACRISS code '%s' — treating as pending_review", code
            )
            return ClassificationResult(
                acriss_category=None,
                acriss_body_type=None,
                acriss_transmission=None,
                acriss_fuel=None,
                confidence=0.0,
                pending_review=True,
                rationale=f"LLM returned unknown ACRISS code '{code}'",
            )
        return result

    def _pending_review_result(
        self, rationale: str | None = None
    ) -> ClassificationResult:
        return ClassificationResult(
            acriss_category=None,
            acriss_body_type=None,
            acriss_transmission=None,
            acriss_fuel=None,
            confidence=0.0,
            pending_review=True,
            rationale=rationale,
        )

    def _build_batch_prompt(
        self, provider_code: str, vehicles: list[VehicleClassificationInput]
    ) -> str:
        acriss_block = "\n\n".join(
            self._render_acriss(spec) for spec in self._acriss_types
        )
        vehicles_block = "\n\n".join(
            f"### Vehicle {i + 1}\n{self._render_batch_vehicle(v)}"
            for i, v in enumerate(vehicles)
        )
        return (
            "You are a vehicle rental classification specialist.\n\n"
            f"Provider code: {provider_code}\n\n"
            "Classify each vehicle below into ONE of the ACRISS codes listed.\n"
            "Each ACRISS code encodes 4 orthogonal attributes:\n"
            "  Position 1: vehicle category (E=Economy, C=Compact, I=Intermediate, etc.)\n"
            "  Position 2: body type (D=2/4-door, G=SUV, M=Minivan, V=Van, etc.)\n"
            "  Position 3: transmission (M=Manual, A=Automatic)\n"
            "  Position 4: fuel/drive (R=Unspecified, H=Hybrid, E=Electric, etc.)\n\n"
            "Use the price information to help distinguish within-provider price tiers.\n"
            "If no ACRISS code fits well, respond with confidence < 0.85 and null for "
            "all four attributes.\n\n"
            f"## ACRISS CODES\n\n{acriss_block}\n\n"
            f"## VEHICLES TO CLASSIFY\n\n{vehicles_block}\n\n"
            "## OUTPUT\n\n"
            "Respond in JSON as an array with one object per vehicle, in the same order:\n"
            "  [{\n"
            "    \"acriss_category\": \"E\",\n"
            "    \"acriss_body_type\": \"D\",\n"
            "    \"acriss_transmission\": \"M\",\n"
            "    \"acriss_fuel\": \"R\",\n"
            "    \"confidence\": 0.95,\n"
            "    \"rationale\": \"...\"\n"
            "  }, ...]\n\n"
            "JSON only. No prose."
        )

    def _render_acriss(self, spec: AcrissCodeSpec) -> str:
        criteria_block = "\n".join(f"  - {c}" for c in spec.criteria)
        examples_block = ", ".join(spec.examples)
        return (
            f"### {spec.code} — {spec.display_name}\n"
            f"Category={spec.category}, Body={spec.body_type}, "
            f"Transmission={spec.transmission}, Fuel={spec.fuel}\n"
            f"{spec.description}\n"
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
            lines.append(f"Transmission hint: {v.transmission}")
        if v.fuel_type:
            lines.append(f"Fuel hint: {v.fuel_type}")
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
