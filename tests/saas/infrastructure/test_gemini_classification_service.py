"""Unit tests for GeminiClassificationService.

All tests mock _call_flash_batch and/or _call_pro_batch — no real Gemini API
calls are made.  A fake API key is used so the constructor's key-presence
check passes without hitting the network.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.saas.application.classification.dtos import ClassificationResult, VehicleClassificationInput
from src.saas.infrastructure.classification.gemini_service import (
    AcrissCodeSpec,
    GeminiClassificationService,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_FAKE_API_KEY = "fake-api-key-for-tests"
_PROVIDER_CODE = "provider_a"

_ACRISS_TYPES = [
    AcrissCodeSpec(
        code="EDMR",
        display_name="Económico Manual",
        description="Sub-compact city car, manual transmission, combustion engine.",
        criteria=["5 seats", "manual transmission"],
        examples=["Fiat Panda", "Kia Picanto"],
    ),
    AcrissCodeSpec(
        code="CGAR",
        display_name="SUV Urbano Automático",
        description="Compact crossover, automatic transmission.",
        criteria=["5 seats", "automatic transmission", "crossover body"],
        examples=["Ford Puma", "Renault Captur"],
    ),
    AcrissCodeSpec(
        code="IFAR",
        display_name="Intermediate SUV Automático",
        description="Intermediate SUV body, automatic transmission.",
        criteria=["5 seats", "automatic transmission", "SUV body"],
        examples=["VW Tiguan", "Hyundai Tucson"],
    ),
    AcrissCodeSpec(
        code="LVAR",
        display_name="Furgoneta Pasajero 9 Plazas Automático",
        description="9-seat passenger vans, automatic.",
        criteria=["9 seats", "automatic"],
        examples=["Mercedes Vito 9p"],
    ),
]


def _make_service(
    acriss_types=None,
    api_key: str = _FAKE_API_KEY,
) -> GeminiClassificationService:
    return GeminiClassificationService(
        acriss_types=acriss_types if acriss_types is not None else _ACRISS_TYPES,
        api_key=api_key,
    )


def _vehicle(**overrides) -> VehicleClassificationInput:
    defaults = dict(
        external_code=None,
        external_name=None,
        example_models="Test Car",
        seats=5,
        luggage=2,
        transmission="manual",
        fuel_type=None,
        representative_price_7d=50.0,
        representative_currency="EUR",
    )
    defaults.update(overrides)
    return VehicleClassificationInput(**defaults)


def _result(
    acriss_code: str | None,
    confidence: float,
    pending_review: bool = False,
    rationale: str | None = None,
) -> ClassificationResult:
    if acriss_code and len(acriss_code) == 4:
        return ClassificationResult(
            acriss_category=acriss_code[0],
            acriss_body_type=acriss_code[1],
            acriss_transmission=acriss_code[2],
            acriss_fuel=acriss_code[3],
            confidence=confidence,
            pending_review=pending_review,
            rationale=rationale,
        )
    return ClassificationResult(
        acriss_category=None,
        acriss_body_type=None,
        acriss_transmission=None,
        acriss_fuel=None,
        confidence=confidence,
        pending_review=pending_review,
        rationale=rationale,
    )


# ---------------------------------------------------------------------------
# Test 3 — Flash confident: return its result, Pro not called
# ---------------------------------------------------------------------------

class TestFlashConfident:
    def test_returns_flash_result_and_skips_pro(self):
        svc = _make_service()
        vehicles = [_vehicle()]
        flash_return = [_result("EDMR", 0.95)]

        with patch.object(svc, "_call_flash_batch", return_value=flash_return) as mock_flash, \
             patch.object(svc, "_call_pro_batch") as mock_pro:
            results = svc.classify_provider_batch(_PROVIDER_CODE, vehicles)

        assert len(results) == 1
        assert results[0].acriss_category == "E"
        assert results[0].acriss_body_type == "D"
        assert results[0].acriss_transmission == "M"
        assert results[0].acriss_fuel == "R"
        assert results[0].confidence == 0.95
        assert results[0].pending_review is False
        mock_flash.assert_called_once_with(_PROVIDER_CODE, vehicles)
        mock_pro.assert_not_called()


# ---------------------------------------------------------------------------
# Test 4 — Flash below threshold, Pro escalation succeeds
# ---------------------------------------------------------------------------

class TestEscalatesToPro:
    def test_escalates_when_flash_below_threshold(self):
        svc = _make_service()
        vehicles = [_vehicle()]
        flash_return = [_result("EDMR", 0.70)]
        pro_return = [_result("CGAR", 0.92)]

        with patch.object(svc, "_call_flash_batch", return_value=flash_return), \
             patch.object(svc, "_call_pro_batch", return_value=pro_return) as mock_pro:
            results = svc.classify_provider_batch(_PROVIDER_CODE, vehicles)

        assert results[0].acriss_category == "C"
        assert results[0].acriss_body_type == "G"
        assert results[0].confidence == 0.92
        assert results[0].pending_review is False
        mock_pro.assert_called_once_with(_PROVIDER_CODE, vehicles)


# ---------------------------------------------------------------------------
# Test 5 — Both below threshold → pending_review with max confidence
# ---------------------------------------------------------------------------

class TestBothBelowThreshold:
    def test_pending_review_with_best_code_and_max_confidence(self):
        """Both Flash and Pro below threshold: Pro's valid code is preserved with pending_review."""
        svc = _make_service()
        vehicles = [_vehicle()]
        flash_return = [_result("EDMR", 0.60)]
        pro_return = [_result("CGAR", 0.70)]

        with patch.object(svc, "_call_flash_batch", return_value=flash_return), \
             patch.object(svc, "_call_pro_batch", return_value=pro_return):
            results = svc.classify_provider_batch(_PROVIDER_CODE, vehicles)

        assert results[0].acriss_category == "C"   # Pro's CGAR preserved
        assert results[0].acriss_body_type == "G"
        assert results[0].pending_review is True
        assert results[0].confidence == pytest.approx(0.70)


# ---------------------------------------------------------------------------
# Test 6 — Flash raises exception → pending_review, Pro not called
# ---------------------------------------------------------------------------

class TestFlashFails:
    def test_returns_pending_review_when_flash_raises(self):
        svc = _make_service()
        vehicles = [_vehicle()]

        with patch.object(svc, "_call_flash_batch", side_effect=RuntimeError("network error")), \
             patch.object(svc, "_call_pro_batch") as mock_pro:
            results = svc.classify_provider_batch(_PROVIDER_CODE, vehicles)

        assert len(results) == 1
        assert results[0].acriss_category is None
        assert results[0].pending_review is True
        assert results[0].confidence == 0.0
        # Transport failure is marked as such — consumers use this to avoid
        # caching an outage as a classification.
        assert results[0].error == "network error"
        mock_pro.assert_not_called()


# ---------------------------------------------------------------------------
# Test 7 — Flash below threshold, Pro raises → pending_review with Flash confidence
# ---------------------------------------------------------------------------

class TestProFallbackFails:
    def test_pending_review_with_flash_code_preserved_when_pro_raises(self):
        """Pro raises: Flash's valid code is preserved (not nulled) with pending_review."""
        svc = _make_service()
        vehicles = [_vehicle()]
        flash_return = [_result("EDMR", 0.70)]

        with patch.object(svc, "_call_flash_batch", return_value=flash_return), \
             patch.object(svc, "_call_pro_batch", side_effect=RuntimeError("rate limit")):
            results = svc.classify_provider_batch(_PROVIDER_CODE, vehicles)

        assert results[0].acriss_category == "E"   # EDMR preserved from Flash
        assert results[0].acriss_body_type == "D"
        assert results[0].pending_review is True
        assert results[0].confidence == pytest.approx(0.70)
        # Flash DID answer — this is a real (low-confidence) classification,
        # not a transport failure: cacheable, so no error mark.
        assert results[0].error is None


# ---------------------------------------------------------------------------
# Test 8 — Flash returns unknown ACRISS code → pending_review, Pro not called
# ---------------------------------------------------------------------------

class TestUnknownAcrissCode:
    def test_rejects_unknown_code_from_llm(self):
        svc = _make_service()
        vehicles = [_vehicle()]
        # "ZZZZ" is not in _ACRISS_TYPES — _validate_code will reject it
        flash_return = [ClassificationResult(
            acriss_category="Z",
            acriss_body_type="Z",
            acriss_transmission="Z",
            acriss_fuel="Z",
            confidence=0.95,
            pending_review=False,
        )]

        with patch.object(svc, "_call_flash_batch", return_value=flash_return), \
             patch.object(svc, "_call_pro_batch") as mock_pro:
            results = svc.classify_provider_batch(_PROVIDER_CODE, vehicles)

        assert results[0].acriss_category is None
        assert results[0].pending_review is True
        assert results[0].confidence == 0.0
        mock_pro.assert_not_called()


# ---------------------------------------------------------------------------
# Test 10 — missing API key raises ValueError
# ---------------------------------------------------------------------------

class TestRequiresApiKey:
    def test_raises_when_no_api_key(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with pytest.raises(ValueError, match="GEMINI_API_KEY"):
            GeminiClassificationService(
                acriss_types=_ACRISS_TYPES,
            )


# ---------------------------------------------------------------------------
# Test 11 — empty acriss_types raises ValueError
# ---------------------------------------------------------------------------

class TestRequiresAcrissTypes:
    def test_raises_when_acriss_types_empty(self):
        with pytest.raises(ValueError, match="acriss_types cannot be empty"):
            GeminiClassificationService(
                acriss_types=[],
                api_key=_FAKE_API_KEY,
            )


# ---------------------------------------------------------------------------
# Test 12 — _build_batch_prompt includes all ACRISS codes and vehicle attributes
# ---------------------------------------------------------------------------

class TestBuildBatchPrompt:
    def test_prompt_includes_all_acriss_codes_and_vehicle_attributes(self):
        svc = _make_service()
        vehicles = [VehicleClassificationInput(
            example_models="Fiat Panda",
            seats=5,
            luggage=2,
            transmission="manual",
            fuel_type="gasoline",
            external_code="A",
            external_name="Economy Group",
            representative_price_7d=45.50,
            representative_currency="EUR",
        )]
        prompt = svc._build_batch_prompt(_PROVIDER_CODE, vehicles)

        for spec in _ACRISS_TYPES:
            assert spec.code in prompt
            for criterion in spec.criteria:
                assert criterion in prompt
            for example in spec.examples:
                assert example in prompt

        assert "Fiat Panda" in prompt
        assert "5" in prompt
        assert "2" in prompt
        assert "manual" in prompt
        assert "gasoline" in prompt
        assert "Economy Group" in prompt
        assert "45.50" in prompt
        assert "EUR" in prompt
        assert _PROVIDER_CODE in prompt


# ---------------------------------------------------------------------------
# Test 13 — acriss_loader parses the real acriss_codes.yaml
# ---------------------------------------------------------------------------

class TestAcrissLoader:
    def test_parses_real_acriss_codes_yaml(self):
        from pathlib import Path
        from src.saas.application.classification.acriss_loader import load_acriss_specs

        yaml_path = Path(__file__).resolve().parents[3] / "acriss_codes.yaml"
        specs = load_acriss_specs(yaml_path)

        codes = {s.code for s in specs}
        assert len(specs) == len(codes)  # no duplicate codes in the yaml
        assert len(codes) >= 90
        assert "EDMR" in codes
        assert "EFMR" in codes
        # Body G materialized since engine v2 (DATA_MODEL.md Decision 12),
        # plus PHEV (I) and diesel (D) fuel codes.
        assert {"CGAR", "IGAR", "IFAI", "CDMD"} <= codes
        # Compact van family: passenger ludospace (V) + commercial/cargo (K).
        assert {"CVMR", "CVAR", "CKMR", "CKAR"} <= codes
        eco = next(s for s in specs if s.code == "EDMR")
        assert eco.description
        assert len(eco.examples) > 0


# ---------------------------------------------------------------------------
# Test — ACRISS reference documentation is loaded on service init
# ---------------------------------------------------------------------------

class TestReferenceLoaded:
    def test_loads_acriss_reference_on_init(self):
        svc = _make_service()
        assert svc._acriss_reference, "Reference text must be non-empty"
        assert "ACRISS" in svc._acriss_reference
        assert "Position 1" in svc._acriss_reference

    def test_reference_contains_key_sections(self):
        svc = _make_service()
        ref = svc._acriss_reference
        assert "CATEGORY" in ref
        assert "BODY TYPE" in ref
        assert "TRANSMISSION" in ref
        assert "FUEL" in ref


# ---------------------------------------------------------------------------
# Test — _build_batch_prompt has all four sections
# ---------------------------------------------------------------------------

class TestBuildBatchPromptSections:
    def test_prompt_has_four_sections(self):
        svc = _make_service()
        vehicles = [_vehicle()]
        prompt = svc._build_batch_prompt(_PROVIDER_CODE, vehicles)

        assert "ACRISS Code Reference" in prompt       # section 1: reference doc header
        assert "MATERIALIZED CODES" in prompt          # section 2: available codes
        assert "VEHICLES TO CLASSIFY" in prompt        # section 3: vehicle data
        assert "OUTPUT FORMAT" in prompt               # section 4: output instructions

    def test_prompt_output_section_includes_new_fields(self):
        svc = _make_service()
        prompt = svc._build_batch_prompt(_PROVIDER_CODE, [_vehicle()])

        assert "acriss_code" in prompt      # new top-level code field
        assert "pending_review" in prompt   # explicit flag in output
        assert "reasoning" in prompt        # rationale field name

    def test_prompt_includes_mixed_groups_reminder(self):
        svc = _make_service()
        prompt = svc._build_batch_prompt(_PROVIDER_CODE, [_vehicle()])

        assert "CRITICAL RULE FOR MIXED GROUPS" in prompt
        assert "ABSOLUTE RULE" in prompt
        assert "VW Tiguan" in prompt   # few-shot example present

    def test_mixed_groups_reminder_positioned_after_vehicles(self):
        svc = _make_service()
        prompt = svc._build_batch_prompt(_PROVIDER_CODE, [_vehicle()])

        vehicles_pos = prompt.index("VEHICLES TO CLASSIFY")
        reminder_pos = prompt.index("CRITICAL RULE FOR MIXED GROUPS")
        output_pos = prompt.index("OUTPUT FORMAT")

        assert vehicles_pos < reminder_pos < output_pos

    def test_prompt_uses_best_guess_wording(self):
        """Prompt must instruct best-guess over null; no 'null if no fit' instruction."""
        svc = _make_service()
        prompt = svc._build_batch_prompt(_PROVIDER_CODE, [_vehicle()])

        assert "best guess" in prompt
        assert "pending_review is ALWAYS preferred over null" in prompt
        assert "null and pending_review=true" not in prompt
        assert "confidence < 0.70" not in prompt


# ---------------------------------------------------------------------------
# Test — _parse_llm_item post-LLM validation
# ---------------------------------------------------------------------------

class TestParseLlmItem:
    def test_valid_code_produces_correct_result(self):
        svc = _make_service()
        item = {
            "acriss_code": "EDMR",
            "acriss_category": "E",
            "acriss_body_type": "D",
            "acriss_transmission": "M",
            "acriss_fuel": "R",
            "confidence": 0.95,
            "reasoning": "Clear economy manual",
            "pending_review": False,
        }
        result = svc._parse_llm_item(item)
        assert result.acriss_category == "E"
        assert result.acriss_body_type == "D"
        assert result.acriss_transmission == "M"
        assert result.acriss_fuel == "R"
        assert result.confidence == pytest.approx(0.95)
        assert result.pending_review is False
        assert result.rationale == "Clear economy manual"

    def test_hallucinated_code_produces_pending_review(self):
        svc = _make_service()
        item = {
            "acriss_code": "ZZZZ",
            "confidence": 0.92,
            "reasoning": "Hallucinated code",
            "pending_review": False,
        }
        result = svc._parse_llm_item(item)
        assert result.acriss_category is None
        assert result.pending_review is True
        assert result.confidence == pytest.approx(0.0)  # confidence zeroed on hallucination

    def test_inconsistent_attrs_corrected_from_code(self):
        svc = _make_service()
        # LLM returns the correct code but wrong individual attrs
        item = {
            "acriss_code": "EDMR",
            "acriss_category": "C",   # wrong — should be E
            "acriss_body_type": "G",  # wrong — should be D
            "acriss_transmission": "A",  # wrong — should be M
            "acriss_fuel": "H",       # wrong — should be R
            "confidence": 0.90,
            "reasoning": "Inconsistent attrs test",
            "pending_review": False,
        }
        result = svc._parse_llm_item(item)
        # Code takes precedence — attrs derived from EDMR chars
        assert result.acriss_category == "E"
        assert result.acriss_body_type == "D"
        assert result.acriss_transmission == "M"
        assert result.acriss_fuel == "R"
        assert result.pending_review is False

    def test_low_confidence_code_preserved_with_pending_review(self):
        """A valid catalog code with low confidence is kept; only pending_review flips to True."""
        svc = _make_service()
        item = {
            "acriss_code": "EDMR",
            "confidence": 0.60,
            "reasoning": "Very uncertain",
            "pending_review": False,
        }
        result = svc._parse_llm_item(item)
        assert result.acriss_category == "E"
        assert result.acriss_body_type == "D"
        assert result.acriss_transmission == "M"
        assert result.acriss_fuel == "R"
        assert result.pending_review is True
        assert result.confidence == pytest.approx(0.60)

    def test_null_code_produces_pending_review(self):
        svc = _make_service()
        item = {
            "acriss_code": None,
            "confidence": 0.60,
            "reasoning": "No good match found",
            "pending_review": True,
        }
        result = svc._parse_llm_item(item)
        assert result.acriss_category is None
        assert result.pending_review is True
        assert result.confidence == pytest.approx(0.60)

    def test_missing_code_key_produces_pending_review(self):
        svc = _make_service()
        item = {"confidence": 0.50, "reasoning": "No code key at all"}
        result = svc._parse_llm_item(item)
        assert result.acriss_category is None
        assert result.pending_review is True

    def test_reasoning_field_mapped_to_rationale(self):
        svc = _make_service()
        item = {
            "acriss_code": "EDMR",
            "confidence": 0.90,
            "reasoning": "This is the reasoning text",
        }
        result = svc._parse_llm_item(item)
        assert result.rationale == "This is the reasoning text"


# ---------------------------------------------------------------------------
# Test — mixed confidence batch: Pro called, Pro results used for all vehicles
# ---------------------------------------------------------------------------

class TestMixedConfidenceBatch:
    def test_classify_provider_batch_handles_mixed_confidence(self):
        svc = _make_service()
        vehicles = [
            _vehicle(external_code="EA", representative_price_7d=57.0),
            _vehicle(external_code="GA", representative_price_7d=69.0),
        ]
        flash_results = [
            _result("EDMR", 0.95),
            _result("CGAR", 0.70),
        ]
        pro_results = [
            _result("EDMR", 0.88),
            _result("CGAR", 0.91),
        ]

        with patch.object(svc, "_call_flash_batch", return_value=flash_results) as mock_flash, \
             patch.object(svc, "_call_pro_batch", return_value=pro_results) as mock_pro:
            results = svc.classify_provider_batch(_PROVIDER_CODE, vehicles)

        mock_flash.assert_called_once()
        mock_pro.assert_called_once()

        assert results[0].acriss_category == "E"
        assert results[0].confidence == pytest.approx(0.88)
        assert results[0].pending_review is False

        assert results[1].acriss_category == "C"
        assert results[1].confidence == pytest.approx(0.91)
        assert results[1].pending_review is False


# ---------------------------------------------------------------------------
# Test — mixed groups: LLM sets pending_review=true + valid code → code preserved
# ---------------------------------------------------------------------------

class TestMixedGroup:
    """Scenario: a PVC groups models that map to different ACRISS codes.

    The LLM applies MIXED_GROUPS_REMINDER: picks the highest-tier model and
    returns pending_review=true with confidence≈0.65.  The service must
    preserve the code rather than nulling it.
    """

    def test_parse_llm_item_preserves_code_for_mixed_group(self):
        """_parse_llm_item: LLM pending_review=true + valid code → code kept."""
        svc = _make_service()
        item = {
            "acriss_code": "IFAR",
            "acriss_category": "I",
            "acriss_body_type": "F",
            "acriss_transmission": "A",
            "acriss_fuel": "R",
            "confidence": 0.65,
            "reasoning": (
                "VW Tiguan (IFAR, Intermediate SUV) and VW T-Roc (CGAR, Compact "
                "Crossover) are present. Tiguan is the highest-tier model; "
                "classifying to IFAR and flagging for review."
            ),
            "pending_review": True,
        }
        result = svc._parse_llm_item(item)

        assert result.acriss_category == "I"
        assert result.acriss_body_type == "F"
        assert result.acriss_transmission == "A"
        assert result.acriss_fuel == "R"
        assert result.confidence == pytest.approx(0.65)
        assert result.pending_review is True

    def test_classify_provider_batch_passes_through_mixed_group_result(self):
        """Full batch flow: Flash returns mixed-group result; preserved, Pro not called."""
        svc = _make_service()
        vehicles = [_vehicle(example_models="VW Tiguan, VW T-Roc")]
        flash_return = [ClassificationResult(
            acriss_category="I",
            acriss_body_type="F",
            acriss_transmission="A",
            acriss_fuel="R",
            confidence=0.65,
            pending_review=True,
            rationale="Mixed group: Tiguan (IFAR) vs T-Roc (CGAR); highest tier chosen.",
        )]

        with patch.object(svc, "_call_flash_batch", return_value=flash_return) as mock_flash, \
             patch.object(svc, "_call_pro_batch") as mock_pro:
            results = svc.classify_provider_batch(_PROVIDER_CODE, vehicles)

        assert results[0].acriss_category == "I"
        assert results[0].acriss_body_type == "F"
        assert results[0].confidence == pytest.approx(0.65)
        assert results[0].pending_review is True
        mock_flash.assert_called_once()
        mock_pro.assert_not_called()  # pending_review=True skips Pro escalation
