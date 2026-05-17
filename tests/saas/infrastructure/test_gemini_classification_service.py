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
    def test_pending_review_with_max_confidence(self):
        svc = _make_service()
        vehicles = [_vehicle()]
        flash_return = [_result("EDMR", 0.60)]
        pro_return = [_result("CGAR", 0.70)]

        with patch.object(svc, "_call_flash_batch", return_value=flash_return), \
             patch.object(svc, "_call_pro_batch", return_value=pro_return):
            results = svc.classify_provider_batch(_PROVIDER_CODE, vehicles)

        assert results[0].acriss_category is None
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
        mock_pro.assert_not_called()


# ---------------------------------------------------------------------------
# Test 7 — Flash below threshold, Pro raises → pending_review with Flash confidence
# ---------------------------------------------------------------------------

class TestProFallbackFails:
    def test_pending_review_with_flash_confidence_when_pro_raises(self):
        svc = _make_service()
        vehicles = [_vehicle()]
        flash_return = [_result("EDMR", 0.70)]

        with patch.object(svc, "_call_flash_batch", return_value=flash_return), \
             patch.object(svc, "_call_pro_batch", side_effect=RuntimeError("rate limit")):
            results = svc.classify_provider_batch(_PROVIDER_CODE, vehicles)

        assert results[0].acriss_category is None
        assert results[0].pending_review is True
        assert results[0].confidence == pytest.approx(0.70)


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

        assert len(specs) == 26
        codes = {s.code for s in specs}
        assert "EDMR" in codes
        assert "CGAR" in codes
        eco = next(s for s in specs if s.code == "EDMR")
        assert eco.description
        assert len(eco.examples) > 0


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
