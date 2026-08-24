"""Unit tests for AcrissEngineClassificationService (engine v2 integration
layer): bundles, provider-declared codes, semantic fallback, catalog guard.
No DB, no LLM — everything injected."""
from __future__ import annotations

from src.saas.application.classification.dtos import VehicleClassificationInput
from src.saas.infrastructure.classification.engine_service import (
    AcrissEngineClassificationService,
)


def vin(example_models, external_code=None, transmission=None, seats=None,
        external_name=None):
    return VehicleClassificationInput(
        external_code=external_code,
        external_name=external_name,
        example_models=example_models,
        seats=seats,
        luggage=None,
        transmission=transmission,
        fuel_type=None,
        representative_price_7d=None,
        representative_currency=None,
    )


def make_service(codes, resolver=None):
    return AcrissEngineClassificationService(
        materialized_codes=codes, resolver=resolver, session_factory=None
    )


class TestSingleModel:
    def test_known_model_full_result(self):
        svc = make_service({"CGAR"})
        [r] = svc.classify_provider_batch(
            "prov", [vin("Volkswagen T-Cross", transmission="automatic")]
        )
        assert (r.acriss_category, r.acriss_body_type,
                r.acriss_transmission, r.acriss_fuel) == ("C", "G", "A", "R")
        assert r.detail["letters"]["type"]["source"] == "model_dictionary"
        assert r.error is None


class TestBundles:
    def test_mixed_bundle_prices_to_most_expensive(self):
        svc = make_service({"IFMR", "MDMR"})
        [r] = svc.classify_provider_batch(
            "prov", [vin("Fiat 500, Volkswagen Tiguan", transmission="manual")]
        )
        # Tiguan (I) outranks Fiat 500 (M) on the category scale.
        assert r.acriss_category == "I"
        assert r.acriss_body_type == "F"
        assert r.pending_review is True
        assert r.confidence <= 0.65
        assert any("Mixed bundle" in a for a in r.detail["assumptions"])

    def test_identical_bundle_not_marked_mixed(self):
        svc = make_service({"EDMR"})
        [r] = svc.classify_provider_batch(
            "prov", [vin("SEAT Ibiza, Skoda Fabia", transmission="manual")]
        )
        assert r.acriss_category == "E"
        assert r.confidence > 0.65


class TestProviderDeclaredCode:
    def test_declared_code_fills_missing_transmission(self):
        svc = make_service({"MDMR"})
        [r] = svc.classify_provider_batch(
            "recordgo",
            [vin("Fiat 500 o similar MBMR", external_code="MBMR", transmission=None)],
        )
        assert r.acriss_transmission == "M"
        assert r.acriss_category == "M"   # engine dictionary, not the declared 'M'
        assert r.acriss_fuel == "R"
        assert r.detail["letters"]["transmission"]["source"] == "source_override"

    def test_declared_code_ignored_when_not_acriss_shaped(self):
        svc = make_service({"EDMR"})
        [r] = svc.classify_provider_batch(
            "prov", [vin("SEAT Ibiza", external_code="GRP-2", transmission="manual")]
        )
        assert r.acriss_transmission == "M"


class TestMaterializationGuard:
    def test_unmaterialized_code_becomes_null_pending(self):
        # LVMR NOT in the injected catalog: the engine's code cannot be
        # persisted (FK) — NULLs + pending, detail keeps the proposal.
        svc = make_service({"CGAR"})
        [r] = svc.classify_provider_batch(
            "prov", [vin("Toyota Proace Verso", transmission="manual", seats=9)]
        )
        assert r.acriss_category is None
        assert r.pending_review is True
        assert r.detail["unmaterialized_code"] == "LVMR"
        assert r.detail["partial_acriss"] == "LVMR"


class FakeResolver:
    def __init__(self, profiles):
        self.profiles = profiles
        self.calls = []

    def resolve(self, names):
        self.calls.append(names)
        return self.profiles


class TestSemanticFallback:
    def test_unknown_model_resolved_semantically(self):
        resolver = FakeResolver([{
            "likely_category": "I", "likely_type": "G",
            "powertrain_profile": "ice_dominant",
            "confidence": 0.95, "reason": "C-SUV",
        }])
        svc = make_service({"IGMR"}, resolver=resolver)
        [r] = svc.classify_provider_batch(
            "prov", [vin("Chery Tiggo 4", transmission="manual")]
        )
        assert resolver.calls == [["Chery Tiggo 4"]]
        assert r.acriss_category == "I"
        assert r.acriss_body_type == "G"
        # LLM-derived letters are capped below the review threshold
        assert r.detail["letters"]["category"]["confidence"] <= 0.84
        assert r.pending_review is True

    def test_known_models_never_hit_resolver(self):
        resolver = FakeResolver([])
        svc = make_service({"CGAR"}, resolver=resolver)
        svc.classify_provider_batch(
            "prov", [vin("Volkswagen T-Cross", transmission="automatic")]
        )
        assert resolver.calls == []
