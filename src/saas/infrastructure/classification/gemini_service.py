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
from pathlib import Path

from google import genai
from google.genai import types

from src.saas.application.classification.dtos import (
    ClassificationResult,
    VehicleClassificationInput,
)
from src.saas.application.classification.service import ClassificationService

_CONFIDENCE_THRESHOLD = 0.85

# Model selection. ALWAYS pin explicit versions — never floating aliases
# ("-latest") or "-preview" models: the classifier is calibrated (confidence
# thresholds, mixed-group rule) and a model that changes underneath silently
# recalibrates it. Upgrades are deliberate: change the env var (or default),
# which rotates classifier_version and invalidates the model_classifications
# cache; scripts/reclassify_compare.py diffs old vs new before adopting.
_DEFAULT_FLASH_MODEL = "gemini-3.6-flash"
_DEFAULT_PRO_MODEL = "gemini-2.5-pro"  # newest STABLE Pro; 3.x Pro only in preview


def flash_model() -> str:
    """Primary (cheap) classification model. Override: GEMINI_FLASH_MODEL."""
    return os.getenv("GEMINI_FLASH_MODEL", _DEFAULT_FLASH_MODEL)


def pro_model() -> str:
    """Escalation model for low-confidence batches. Override: GEMINI_PRO_MODEL."""
    return os.getenv("GEMINI_PRO_MODEL", _DEFAULT_PRO_MODEL)

# Bump when the classification PROMPT/logic changes (mixed-group rule, fuel
# fallback, tie-breaks…). Combined with a hash of acriss_codes.yaml, it forms the
# classifier_version that invalidates the model_classifications cache.
PROMPT_VERSION = "1"
_REFERENCE_PATH = Path(__file__).parents[4] / "docs" / "acriss_reference.md"

_MIXED_GROUPS_REMINDER = """\
CRITICAL RULE FOR MIXED GROUPS:

When a PVC groups multiple distinct vehicle models that map to DIFFERENT
ACRISS codes, you MUST:
  - Set acriss_code to the code of the model with the HIGHEST EXPECTED RENTAL
    PRICE in the bundle (NEVER null).
  - Set pending_review = true.
  - Set confidence = 0.65.
  - In reasoning, explain which models are present, which codes they
    individually map to, and which one you judged the most expensive (and why).

RATIONALE: a bundle means "<model> or similar"; the provider may deliver any of
them, so we price to the MOST EXPENSIVE option offered — never undershoot. If the
provider advertises the dearer car and then delivers a cheaper one, that is the
provider's problem, not a pricing error on our side. (This is the OPPOSITE of a
cheapest-wins rule: do NOT pick the floor of the bundle.)

HOW TO PICK THE MOST EXPENSIVE MODEL — use your real-world knowledge of each
model's market/rental positioning, weighing BOTH factors together:
  - Segment / size: bigger usually costs more
    (mini < economy < compact < intermediate < standard < fullsize …); within a
    size, SUV/MPV/van ≳ wagon ≳ sedan/hatchback; automatic > manual; and
    electric/hybrid usually carry a premium.
  - Brand prestige: a PREMIUM brand usually costs more than a mainstream brand of
    the same size. DO NOT discard premium models — they are often the dearest
    option and may out-price a larger mainstream car.
    Premium brands (closed list): Audi, BMW, Mercedes-Benz, Volvo, Lexus, Mini,
    DS, Porsche, Land Rover, Jaguar. (Cupra = mainstream.)
Judge which single model a customer would actually pay the most for, then return
THAT model's exact ACRISS code (it must be one of the materialized codes).

ABSOLUTE RULE: NEVER set acriss_code = null for a mixed group when at least one
of the models matches a materialized code. This OVERRIDES the general "return
null if no fit" guidance. Assign the most-expensive model's code and mark
pending_review = true.

EXAMPLE 1 (size drives price):
  Input PVC: example_models = "VW Tiguan, VW T-Roc"
    - VW Tiguan → IFAR (Intermediate SUV)  ← larger, more expensive
    - VW T-Roc  → CFAR (Compact SUV)
  Most expensive: VW Tiguan → IFAR
  Correct response:
    {
      "acriss_code": "IFAR",
      "acriss_category": "I",
      "acriss_body_type": "F",
      "acriss_transmission": "A",
      "acriss_fuel": "R",
      "confidence": 0.65,
      "pending_review": true,
      "reasoning": "Mixed group: VW Tiguan (IFAR) vs VW T-Roc (CFAR). The Tiguan
                    is the larger, pricier SUV — priced to it (IFAR)."
    }

EXAMPLE 2 (premium counts — do NOT discard it):
  Input PVC: example_models = "Audi A1, Ford Focus, Opel Astra"
    - Audi A1    → HDMR (premium supermini)
    - Ford Focus → CDMR (mainstream compact)
    - Opel Astra → CDMR (mainstream compact)
  Judgement: the premium Audi A1 rents above the mainstream compacts, so it is
  the most expensive option → HDMR.
  Correct response:
    {
      "acriss_code": "HDMR",
      "acriss_category": "H",
      "acriss_body_type": "D",
      "acriss_transmission": "M",
      "acriss_fuel": "R",
      "confidence": 0.65,
      "pending_review": true,
      "reasoning": "Mixed bundle: premium Audi A1 (HDMR) judged dearer than the
                    mainstream Focus/Astra (CDMR); priced to the most expensive,
                    HDMR."
    }

  INCORRECT response (do NOT do this):
    { "acriss_code": null, "pending_review": true, "confidence": 0.65 }
"""

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
        reference_path: Path | None = None,
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
        ref = reference_path or _REFERENCE_PATH
        self._acriss_reference = ref.read_text(encoding="utf-8")
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
            # Transport-level failure: no classification happened. `error` lets
            # consumers (the classify API) distinguish this from a genuine
            # can't-classify outcome and avoid caching it.
            return [
                self._pending_review_result(
                    rationale=f"Flash call failed: {exc}", error=str(exc)
                )
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
                    acriss_category=r.acriss_category,
                    acriss_body_type=r.acriss_body_type,
                    acriss_transmission=r.acriss_transmission,
                    acriss_fuel=r.acriss_fuel,
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
                # Both below threshold: keep best-guess code (Pro preferred if it has one)
                best = pro_r if pro_r.acriss_category is not None else flash_r
                final.append(ClassificationResult(
                    acriss_category=best.acriss_category,
                    acriss_body_type=best.acriss_body_type,
                    acriss_transmission=best.acriss_transmission,
                    acriss_fuel=best.acriss_fuel,
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
        return self._call_model_batch(flash_model(), provider_code, vehicles)

    def _call_pro_batch(
        self, provider_code: str, vehicles: list[VehicleClassificationInput]
    ) -> list[ClassificationResult]:
        return self._call_model_batch(pro_model(), provider_code, vehicles)

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
        return [self._parse_llm_item(item) for item in raw]

    def _parse_llm_item(self, item: dict) -> ClassificationResult:
        """Parse one LLM JSON response item into a ClassificationResult.

        Post-LLM validation rules:
        - acriss_code absent or null → pending_review, null attrs
        - acriss_code not in catalog → pending_review (hallucination), confidence zeroed
        - acriss_code valid, LLM set pending_review=true → preserve code, pending_review=true
          (mixed-group case: most-expensive code chosen, operator review needed)
        - acriss_code valid, confidence < 0.70 → pending_review, null attrs (poor fit)
        - acriss_code valid, confidence ≥ 0.70 → derive 4 attrs from code chars,
          correcting any inconsistency the LLM may have introduced
        """
        confidence = float(item.get("confidence", 0.0))
        rationale = item.get("reasoning") or item.get("rationale")
        acriss_code = item.get("acriss_code")
        llm_pending_review = bool(item.get("pending_review", False))

        if not acriss_code:
            return ClassificationResult(
                acriss_category=None,
                acriss_body_type=None,
                acriss_transmission=None,
                acriss_fuel=None,
                confidence=confidence,
                pending_review=True,
                rationale=rationale,
            )

        if acriss_code not in self._known_codes:
            logger.warning(
                "LLM returned unknown ACRISS code '%s' — treating as pending_review",
                acriss_code,
            )
            return ClassificationResult(
                acriss_category=None,
                acriss_body_type=None,
                acriss_transmission=None,
                acriss_fuel=None,
                confidence=0.0,
                pending_review=True,
                rationale=rationale,
            )

        # LLM explicitly set pending_review (e.g. mixed group per MIXED_GROUPS_REMINDER).
        # Preserve the code as the most-expensive best guess; flag for operator review.
        if llm_pending_review:
            return ClassificationResult(
                acriss_category=acriss_code[0],
                acriss_body_type=acriss_code[1],
                acriss_transmission=acriss_code[2],
                acriss_fuel=acriss_code[3],
                confidence=confidence,
                pending_review=True,
                rationale=rationale,
            )

        # Code is valid and in the catalog. Always keep it; confidence decides pending_review.
        # Attrs derived from the code chars — corrects any inconsistency in LLM-returned attrs.
        return ClassificationResult(
            acriss_category=acriss_code[0],
            acriss_body_type=acriss_code[1],
            acriss_transmission=acriss_code[2],
            acriss_fuel=acriss_code[3],
            confidence=confidence,
            pending_review=confidence < _CONFIDENCE_THRESHOLD,
            rationale=rationale,
        )

    def _validate_code(self, result: ClassificationResult) -> ClassificationResult:
        """Safety net: if the result carries a code not in our catalog, treat as pending_review.

        Primarily relevant when _call_flash_batch/_call_pro_batch are mocked in tests
        or when results are constructed externally.
        """
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
        self, rationale: str | None = None, error: str | None = None
    ) -> ClassificationResult:
        return ClassificationResult(
            acriss_category=None,
            acriss_body_type=None,
            acriss_transmission=None,
            acriss_fuel=None,
            confidence=0.0,
            pending_review=True,
            rationale=rationale,
            error=error,
        )

    def _build_batch_prompt(
        self, provider_code: str, vehicles: list[VehicleClassificationInput]
    ) -> str:
        section_materialized = self._build_materialized_codes_section()
        section_vehicles = self._build_vehicles_section(provider_code, vehicles)
        section_output = self._build_output_format_section(len(vehicles))

        return (
            "You are an expert vehicle classifier for the car rental industry.\n\n"
            f"{self._acriss_reference}\n\n"
            "---\n\n"
            "### MATERIALIZED CODES\n\n"
            "(The ONLY valid codes for this classification. "
            "Do not use any code not listed here.)\n\n"
            f"{section_materialized}\n\n"
            "---\n\n"
            "### VEHICLES TO CLASSIFY\n\n"
            f"{section_vehicles}\n\n"
            "---\n\n"
            f"{_MIXED_GROUPS_REMINDER}\n\n"
            "---\n\n"
            "### OUTPUT FORMAT\n\n"
            f"{section_output}"
        )

    def _build_materialized_codes_section(self) -> str:
        return "\n\n".join(
            self._render_acriss_spec(spec) for spec in self._acriss_types
        )

    def _render_acriss_spec(self, spec: AcrissCodeSpec) -> str:
        criteria_block = "\n".join(f"    - {c}" for c in spec.criteria)
        examples_str = ", ".join(spec.examples)
        return (
            f"- **{spec.code}** — {spec.display_name}\n"
            f"  {spec.description}\n"
            f"  Criteria:\n{criteria_block}\n"
            f"  Examples: {examples_str}"
        )

    def _build_vehicles_section(
        self, provider_code: str, vehicles: list[VehicleClassificationInput]
    ) -> str:
        return "\n\n".join(
            f"#### Vehicle {i + 1} (provider: {provider_code})\n"
            + self._render_vehicle(v)
            for i, v in enumerate(vehicles)
        )

    def _render_vehicle(self, v: VehicleClassificationInput) -> str:
        lines = [f"Models: {v.example_models}"]
        if v.seats is not None:
            lines.append(f"Seats: {v.seats}")
        if v.luggage is not None:
            lines.append(f"Luggage capacity: {v.luggage}")
        if v.transmission:
            lines.append(f"Transmission: {v.transmission}")
        if v.fuel_type:
            lines.append(f"Fuel: {v.fuel_type}")
        if v.external_code:
            lines.append(f"Provider code: {v.external_code}")
        if v.external_name:
            lines.append(f"Provider name: {v.external_name}")
        if v.representative_price_7d is not None:
            currency = v.representative_currency or ""
            price_line = f"Representative 7-day price: {v.representative_price_7d:.2f}"
            if currency:
                price_line += f" {currency}"
            lines.append(price_line)
        return "\n".join(lines)

    def _build_output_format_section(self, n_vehicles: int) -> str:
        example = (
            '  {\n'
            '    "acriss_code": "CFAR",       // 4-char code, or null only if truly unclassifiable\n'
            '    "acriss_category": "C",       // Position 1 of the code\n'
            '    "acriss_body_type": "F",      // Position 2\n'
            '    "acriss_transmission": "A",   // Position 3\n'
            '    "acriss_fuel": "R",           // Position 4\n'
            '    "confidence": 0.95,           // 0.0 to 1.0\n'
            '    "reasoning": "...",           // 1-3 sentences explaining the choice\n'
            '    "pending_review": false       // true if uncertain or mixed group\n'
            '  }'
        )
        return (
            f"Return ONLY a JSON array with exactly {n_vehicles} object(s) "
            "(one per vehicle, in the same order). No markdown wrapper, no commentary:\n\n"
            f"[{example}, ...]\n\n"
            "Rules:\n"
            "- acriss_code MUST be one of the materialized codes listed above. "
            "Do not invent codes not in the list.\n"
            "- ALWAYS assign the closest materialized code as your best guess. "
            "Only set acriss_code=null if the vehicle is genuinely unclassifiable "
            "(e.g. not a car, corrupt data, or no listed code is even remotely applicable).\n"
            "- FUEL FALLBACK: if a vehicle is hybrid or electric but NO materialized "
            "code carries that fuel for its size/body/transmission, use the sibling "
            "ending in R (unspecified fuel) — e.g. a hybrid compact SUV → CFAR, a "
            "hybrid mini → MDMR. NEVER return null just because the fuel-specific "
            "variant (H/E) is absent; the fuel is not worth dropping the vehicle. "
            "This fallback is the INTENDED rule, not uncertainty — classify with "
            "normal confidence and do NOT set pending_review solely because of it.\n"
            "- If uncertain which code fits best, assign the closest one and set "
            "pending_review=true with a confidence reflecting your uncertainty. "
            "A best-guess code with pending_review is ALWAYS preferred over null.\n"
            "- The 4 attributes must match exactly the 4 characters of acriss_code.\n"
        )
