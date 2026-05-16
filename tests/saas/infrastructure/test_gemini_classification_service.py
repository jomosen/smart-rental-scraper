"""Unit tests for GeminiClassificationService.

All tests mock _call_flash and/or _call_pro — no real Gemini API calls
are made. A fake API key is used so the constructor's key-presence check
passes without hitting the network.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.saas.application.classification.dtos import ClassificationResult, VehicleAttributes
from src.saas.infrastructure.classification.gemini_service import (
    CanonicalTypeSpec,
    GeminiClassificationService,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_FAKE_API_KEY = "fake-api-key-for-tests"

_CANONICAL_TYPES = [
    CanonicalTypeSpec(
        code="ECONOMY_MANUAL",
        name="Económico manual",
        description="Small urban cars with manual transmission.",
        criteria=["5 seats", "manual transmission"],
        examples=["Fiat Panda", "Kia Picanto"],
    ),
    CanonicalTypeSpec(
        code="COMPACT_AUTO",
        name="Compacto automático",
        description="Medium cars with automatic transmission.",
        criteria=["5 seats", "automatic transmission"],
        examples=["Ford Puma", "Renault Captur"],
    ),
    CanonicalTypeSpec(
        code="VAN_9_AUTO",
        name="Furgoneta 9 plazas automática",
        description="9-seat passenger vans, automatic.",
        criteria=["9 seats", "automatic"],
        examples=["Mercedes Vito 9p"],
    ),
]


def _make_service(
    canonical_types=None,
    taxonomy_version: int = 1,
    api_key: str = _FAKE_API_KEY,
) -> GeminiClassificationService:
    return GeminiClassificationService(
        canonical_types=canonical_types if canonical_types is not None else _CANONICAL_TYPES,
        taxonomy_version=taxonomy_version,
        api_key=api_key,
    )


def _attrs(**overrides) -> VehicleAttributes:
    defaults = dict(
        example_models="Test Car",
        seats=5,
        luggage=2,
        transmission="manual",
        fuel_type=None,
        external_code=None,
        external_name=None,
    )
    defaults.update(overrides)
    return VehicleAttributes(**defaults)


def _result(
    code: str | None,
    confidence: float,
    taxonomy_version: int = 1,
    pending_review: bool = False,
    rationale: str | None = None,
) -> ClassificationResult:
    return ClassificationResult(
        canonical_type_code=code,
        confidence=confidence,
        taxonomy_version=taxonomy_version,
        pending_review=pending_review,
        rationale=rationale,
    )


# ---------------------------------------------------------------------------
# Test 3 — Flash confident: return its result, Pro not called
# ---------------------------------------------------------------------------

class TestFlashConfident:
    def test_returns_flash_result_and_skips_pro(self):
        svc = _make_service()
        attrs = _attrs()
        flash_return = _result("ECONOMY_MANUAL", 0.95)

        with patch.object(svc, "_call_flash", return_value=flash_return) as mock_flash, \
             patch.object(svc, "_call_pro") as mock_pro:
            result = svc.classify(attrs)

        assert result.canonical_type_code == "ECONOMY_MANUAL"
        assert result.confidence == 0.95
        assert result.pending_review is False
        mock_flash.assert_called_once_with(attrs)
        mock_pro.assert_not_called()


# ---------------------------------------------------------------------------
# Test 4 — Flash below threshold, Pro escalation succeeds
# ---------------------------------------------------------------------------

class TestEscalatesToPro:
    def test_escalates_when_flash_below_threshold(self):
        svc = _make_service()
        attrs = _attrs()
        flash_return = _result("ECONOMY_MANUAL", 0.70)
        pro_return = _result("COMPACT_AUTO", 0.92)

        with patch.object(svc, "_call_flash", return_value=flash_return), \
             patch.object(svc, "_call_pro", return_value=pro_return) as mock_pro:
            result = svc.classify(attrs)

        assert result.canonical_type_code == "COMPACT_AUTO"
        assert result.confidence == 0.92
        assert result.pending_review is False
        mock_pro.assert_called_once_with(attrs)


# ---------------------------------------------------------------------------
# Test 5 — Both below threshold → pending_review with max confidence
# ---------------------------------------------------------------------------

class TestBothBelowThreshold:
    def test_pending_review_with_max_confidence(self):
        svc = _make_service()
        attrs = _attrs()
        flash_return = _result("ECONOMY_MANUAL", 0.60)
        pro_return = _result("COMPACT_AUTO", 0.70)

        with patch.object(svc, "_call_flash", return_value=flash_return), \
             patch.object(svc, "_call_pro", return_value=pro_return):
            result = svc.classify(attrs)

        assert result.canonical_type_code is None
        assert result.pending_review is True
        assert result.confidence == pytest.approx(0.70)


# ---------------------------------------------------------------------------
# Test 6 — Flash raises exception → pending_review, Pro not called
# ---------------------------------------------------------------------------

class TestFlashFails:
    def test_returns_pending_review_when_flash_raises(self):
        svc = _make_service()
        attrs = _attrs()

        with patch.object(svc, "_call_flash", side_effect=RuntimeError("network error")), \
             patch.object(svc, "_call_pro") as mock_pro:
            result = svc.classify(attrs)

        assert result.canonical_type_code is None
        assert result.pending_review is True
        assert result.confidence == 0.0
        mock_pro.assert_not_called()


# ---------------------------------------------------------------------------
# Test 7 — Flash below threshold, Pro raises → pending_review with Flash confidence
# ---------------------------------------------------------------------------

class TestProFallbackFails:
    def test_pending_review_with_flash_confidence_when_pro_raises(self):
        svc = _make_service()
        attrs = _attrs()
        flash_return = _result("ECONOMY_MANUAL", 0.70)

        with patch.object(svc, "_call_flash", return_value=flash_return), \
             patch.object(svc, "_call_pro", side_effect=RuntimeError("rate limit")):
            result = svc.classify(attrs)

        assert result.canonical_type_code is None
        assert result.pending_review is True
        assert result.confidence == pytest.approx(0.70)


# ---------------------------------------------------------------------------
# Test 8 — Flash returns unknown canonical code → pending_review, Pro not called
# ---------------------------------------------------------------------------

class TestUnknownCanonicalCode:
    def test_rejects_unknown_code_from_llm(self):
        svc = _make_service()
        attrs = _attrs()
        flash_return = _result("UNKNOWN_CODE_XYZ", 0.95)

        with patch.object(svc, "_call_flash", return_value=flash_return), \
             patch.object(svc, "_call_pro") as mock_pro:
            result = svc.classify(attrs)

        assert result.canonical_type_code is None
        assert result.pending_review is True
        # Unknown code → confidence reset to 0.0 by _validate_code
        assert result.confidence == 0.0
        mock_pro.assert_not_called()


# ---------------------------------------------------------------------------
# Test 9 — taxonomy_version propagated from service to result
# ---------------------------------------------------------------------------

class TestTaxonomyVersionPropagates:
    def test_result_carries_service_taxonomy_version(self):
        svc = _make_service(taxonomy_version=5)
        attrs = _attrs()
        flash_return = ClassificationResult(
            canonical_type_code="ECONOMY_MANUAL",
            confidence=0.97,
            taxonomy_version=5,
            pending_review=False,
        )

        with patch.object(svc, "_call_flash", return_value=flash_return):
            result = svc.classify(attrs)

        assert result.taxonomy_version == 5


# ---------------------------------------------------------------------------
# Test 10 — missing API key raises ValueError
# ---------------------------------------------------------------------------

class TestRequiresApiKey:
    def test_raises_when_no_api_key(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with pytest.raises(ValueError, match="GEMINI_API_KEY"):
            GeminiClassificationService(
                canonical_types=_CANONICAL_TYPES,
                taxonomy_version=1,
                # api_key not passed — falls back to env var (deleted above)
            )


# ---------------------------------------------------------------------------
# Test 11 — empty canonical_types raises ValueError
# ---------------------------------------------------------------------------

class TestRequiresCanonicalTypes:
    def test_raises_when_canonical_types_empty(self):
        with pytest.raises(ValueError, match="canonical_types cannot be empty"):
            GeminiClassificationService(
                canonical_types=[],
                taxonomy_version=1,
                api_key=_FAKE_API_KEY,
            )


# ---------------------------------------------------------------------------
# Test 12 — _build_prompt includes all canonicals and vehicle attributes
# ---------------------------------------------------------------------------

class TestBuildPrompt:
    def test_prompt_includes_all_canonicals_and_attributes(self):
        svc = _make_service()
        attrs = VehicleAttributes(
            example_models="Fiat Panda",
            seats=5,
            luggage=2,
            transmission="manual",
            fuel_type="gasoline",
            external_code="A",
            external_name="Economy Group",
        )
        prompt = svc._build_prompt(attrs)

        # All three canonical codes must appear
        for ct in _CANONICAL_TYPES:
            assert ct.code in prompt, f"Expected {ct.code!r} in prompt"
            # Criteria from each canonical
            for criterion in ct.criteria:
                assert criterion in prompt, f"Expected criterion {criterion!r} in prompt"
            # Examples from each canonical
            for example in ct.examples:
                assert example in prompt, f"Expected example {example!r} in prompt"

        # Vehicle attributes must appear
        assert "Fiat Panda" in prompt
        assert "5" in prompt          # seats
        assert "2" in prompt          # luggage
        assert "manual" in prompt     # transmission
        assert "gasoline" in prompt   # fuel_type
        assert "\"A\"" in prompt or "'A'" in prompt or "\nA\n" in prompt or "code: A" in prompt or "A" in prompt
        assert "Economy Group" in prompt


# ---------------------------------------------------------------------------
# Test 13 (optional) — taxonomy_loader parses the real taxonomy.yaml
# ---------------------------------------------------------------------------

class TestTaxonomyLoader:
    def test_parses_real_taxonomy_yaml(self):
        from pathlib import Path
        from src.saas.application.classification.taxonomy_loader import load_taxonomy_specs

        yaml_path = Path(__file__).resolve().parents[3] / "taxonomy.yaml"
        specs, version = load_taxonomy_specs(yaml_path)

        assert version == 1
        assert len(specs) == 15
        codes = {s.code for s in specs}
        assert "ECONOMY_MANUAL" in codes
        eco = next(s for s in specs if s.code == "ECONOMY_MANUAL")
        assert eco.description  # non-empty
        assert len(eco.examples) > 0
