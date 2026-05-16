"""Unit tests for classification DTOs and the abstract service interface."""
from __future__ import annotations

from src.saas.application.classification.dtos import ClassificationResult, VehicleAttributes
from src.saas.application.classification.service import ClassificationService


# ---------------------------------------------------------------------------
# Test 1 — ClassificationResult DTO
# ---------------------------------------------------------------------------

class TestClassificationResultDTO:
    def test_holds_all_fields(self):
        result = ClassificationResult(
            canonical_type_code="ECONOMY_MANUAL",
            confidence=0.95,
            taxonomy_version=1,
            pending_review=False,
            rationale="Clearly an economy manual car",
        )
        assert result.canonical_type_code == "ECONOMY_MANUAL"
        assert result.confidence == 0.95
        assert result.taxonomy_version == 1
        assert result.pending_review is False
        assert result.rationale == "Clearly an economy manual car"

    def test_rationale_defaults_to_none(self):
        result = ClassificationResult(
            canonical_type_code=None,
            confidence=0.0,
            taxonomy_version=2,
            pending_review=True,
        )
        assert result.rationale is None
        assert result.canonical_type_code is None
        assert result.pending_review is True

    def test_is_frozen(self):
        result = ClassificationResult(
            canonical_type_code="ECONOMY_MANUAL",
            confidence=0.9,
            taxonomy_version=1,
            pending_review=False,
        )
        try:
            result.confidence = 0.5  # type: ignore[misc]
            assert False, "Should have raised FrozenInstanceError"
        except Exception:
            pass  # frozen=True prevents mutation


# ---------------------------------------------------------------------------
# Test 2 — VehicleAttributes DTO
# ---------------------------------------------------------------------------

class TestVehicleAttributesDTO:
    def test_holds_all_fields(self):
        attrs = VehicleAttributes(
            example_models="Fiat Panda o similar",
            seats=5,
            luggage=2,
            transmission="manual",
            fuel_type="gasoline",
            external_code="A",
            external_name="Economy Group",
        )
        assert attrs.example_models == "Fiat Panda o similar"
        assert attrs.seats == 5
        assert attrs.luggage == 2
        assert attrs.transmission == "manual"
        assert attrs.fuel_type == "gasoline"
        assert attrs.external_code == "A"
        assert attrs.external_name == "Economy Group"

    def test_nullable_fields_accept_none(self):
        attrs = VehicleAttributes(
            example_models="Unknown car",
            seats=None,
            luggage=None,
            transmission=None,
            fuel_type=None,
            external_code=None,
            external_name=None,
        )
        assert attrs.seats is None
        assert attrs.luggage is None
        assert attrs.transmission is None
        assert attrs.fuel_type is None
        assert attrs.external_code is None
        assert attrs.external_name is None

    def test_is_frozen(self):
        attrs = VehicleAttributes(
            example_models="Car",
            seats=5,
            luggage=2,
            transmission="manual",
            fuel_type=None,
            external_code=None,
            external_name=None,
        )
        try:
            attrs.seats = 4  # type: ignore[misc]
            assert False, "Should have raised FrozenInstanceError"
        except Exception:
            pass


# ---------------------------------------------------------------------------
# ClassificationService is abstract — verify it cannot be instantiated
# ---------------------------------------------------------------------------

class TestClassificationServiceIsAbstract:
    def test_cannot_instantiate_directly(self):
        try:
            ClassificationService()  # type: ignore[abstract]
            assert False, "Should have raised TypeError"
        except TypeError:
            pass
