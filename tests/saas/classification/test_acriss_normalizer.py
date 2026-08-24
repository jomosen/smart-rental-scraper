"""Normalization + fuzzy-matching guards (spec §35-§36)."""
from __future__ import annotations

import pytest

from src.saas.application.classification.acriss_engine import (
    VehicleInput,
    classify_vehicle,
    load_dictionary,
)
from src.saas.application.classification.acriss_engine.dictionaries import (
    ModelDictionary,
    _extract_model_designator,
    _model_designator_mismatch,
)
from src.saas.application.classification.acriss_engine.normalizer import (
    norm_key,
    normalize_for_matching,
    strip_noise,
)


# ── §35 All spellings resolve to Volkswagen Golf ──────────────────────────────

@pytest.mark.parametrize("raw", [
    "VW Golf",
    "Volkswagen Golf",
    "VOLKSWAGEN GOLF",
    "Volkswagen Golf or similar",
    "VW GOLF O SIMILAR",
])
def test_golf_spellings_all_resolve(raw):
    r = classify_vehicle(VehicleInput(raw_model=raw))
    assert r.normalized.make == "Volkswagen"
    assert r.normalized.model == "Golf"
    assert r.normalized.key == "volkswagen_golf"


# ── §35 Variant never merges with the hatchback ───────────────────────────────

def test_golf_variant_not_merged_into_golf():
    r = classify_vehicle(VehicleInput(raw_model="Golf Variant"))
    assert r.normalized.key == "volkswagen_golf_variant"


def test_plain_golf_does_not_resolve_variant():
    r = classify_vehicle(VehicleInput(raw_model="Golf"))
    assert r.normalized.key == "volkswagen_golf"


# ── §36 Fuzzy matching is conservative ────────────────────────────────────────

def test_fuzzy_does_not_cross_variant_boundary():
    d = load_dictionary()
    # 'volkswagen golf varian' (typo) may fuzzy-resolve to the variant, but
    # plain 'golf' must never fuzzy-land on 'golf variant'.
    m = d.lookup("golf")
    assert m is not None and m.entry_id == "volkswagen_golf"


def test_fuzzy_typo_accepted_at_high_score():
    d = load_dictionary()
    m = d.lookup("volkswagen glof")  # transposition, ratio >= 0.90
    assert m is not None
    assert m.entry_id == "volkswagen_golf"
    assert m.via == "fuzzy"


def test_low_similarity_is_not_matched():
    d = load_dictionary()
    assert d.lookup("byd dolphin surf") is None


# ── §12 Noise phrases and alias forms ─────────────────────────────────────────

def test_noise_phrases_removed():
    assert "similar" not in strip_noise("Fiat 500 o similar").lower()
    assert "group" not in strip_noise("Group B Fiat 500").lower()


@pytest.mark.parametrize("raw,expected_key", [
    ("T Cross", "volkswagen_t_cross"),
    ("T-Cross", "volkswagen_t_cross"),
    ("TCross", "volkswagen_t_cross"),
    ("Qashqai", "nissan_qashqai"),
    ("Nissan Qashqai", "nissan_qashqai"),
    ("Qashqai SUV", "nissan_qashqai"),
    ("Golf Estate", "volkswagen_golf_variant"),
    ("Golf SW", "volkswagen_golf_variant"),
])
def test_alias_resolution(raw, expected_key):
    r = classify_vehicle(VehicleInput(raw_model=raw))
    assert r.normalized.key == expected_key


def test_norm_key_accents_and_case():
    assert norm_key("Citroën C3") == norm_key("citroen c3")
    assert normalize_for_matching("  SEAT   Ibiza  or similar ") == "seat ibiza"


# ── §36 Model designator extraction ───────────────────────────────────────────

@pytest.mark.parametrize("raw,designator", [
    ("Hyundai i10", "i10"),
    ("Hyundai i20", "i20"),
    ("Hyundai i30", "i30"),
    ("Audi A1", "a1"),
    ("Audi A3", "a3"),
    ("Audi Q2", "q2"),
    ("Audi Q3", "q3"),
    ("Audi Q5", "q5"),
    ("Peugeot 208", "208"),
    ("Peugeot 2008", "2008"),
    ("Fiat 500", "500"),
    ("Fiat 500X", "500x"),
    ("Kia EV3", "ev3"),
    ("Kia EV6", "ev6"),
    ("Kia EV9", "ev9"),
    ("Volkswagen ID.3", "id3"),
    ("Volkswagen ID.4", "id4"),
    ("BMW X1", "x1"),
    ("BMW X3", "x3"),
    ("BMW X5", "x5"),
    # normalization variants collapse to the same designator
    ("ID.4", "id4"),
    ("ID 4", "id4"),
    ("ID4", "id4"),
    ("Hyundai i-20", "i20"),
    ("HYUNDAI I20", "i20"),
    ("Fiat 500-X", "500x"),
    # the make never fuses with the number
    ("BMW 3 Series", "3"),
    ("BMW Serie 3", "3"),
])
def test_extract_model_designator(raw, designator):
    assert _extract_model_designator(norm_key(raw)) == designator


def test_extract_model_designator_absent():
    assert _extract_model_designator(norm_key("Volkswagen Golf")) is None
    assert _extract_model_designator(norm_key("Nissan Qashqai")) is None


# ── §36 Designator guard: different designators must never fuzzy-merge ────────

@pytest.mark.parametrize("query,candidate", [
    ("Hyundai i20", "Hyundai i10"),
    ("Hyundai i30", "Hyundai i10"),
    ("Hyundai i30", "Hyundai i20"),
    ("Fiat 500X", "Fiat 500"),
    ("Peugeot 2008", "Peugeot 208"),
    ("Audi A3", "Audi A1"),
    ("Audi Q3", "Audi Q2"),
    ("Audi Q5", "Audi Q3"),
    ("Kia EV6", "Kia EV3"),
    ("Kia EV9", "Kia EV6"),
    ("Volkswagen ID.4", "Volkswagen ID.3"),
    ("Volkswagen ID.5", "Volkswagen ID.4"),
])
def test_designator_mismatch_rejects(query, candidate):
    assert _model_designator_mismatch(norm_key(query), norm_key(candidate)) is True


@pytest.mark.parametrize("query,candidate", [
    # same family expressed differently — never a mismatch
    ("BMW Serie 3", "BMW 3 Series"),
    ("Volkswagen ID 4", "Volkswagen ID.4"),
    ("Hyundai i-20", "Hyundai i20"),
    # one or both sides without designator — the guard stays out of the way
    ("Volkswagen Golf", "Volkswagen Golf GTI"),
    ("Volkswagen Glof", "Volkswagen Golf"),
    ("Tesla Model 3", "Tesla Model 3"),
])
def test_designator_guard_lets_legitimate_pairs_through(query, candidate):
    assert _model_designator_mismatch(norm_key(query), norm_key(candidate)) is False


# ── §36 Fuzzy lookup honors the guard even when only one sibling exists ───────

def _entry(make: str, model: str, aliases=()) -> dict:
    return {
        "make": make, "model": model, "aliases": list(aliases),
        "category": {"code": "C", "confidence": 0.9},
        "body": {"type": "D", "confidence": 0.9},
        "powertrain_profile": {
            "mode": "ice_only", "default_code": "R", "confidence": 0.9,
        },
        "verification": "strong",
    }


@pytest.mark.parametrize("only_entry,query", [
    (("hyundai_i10", "Hyundai", "i10"), "Hyundai i20"),
    (("hyundai_i10", "Hyundai", "i10"), "Hyundai i30"),
    (("hyundai_i20", "Hyundai", "i20"), "Hyundai i30"),
    (("fiat_500", "Fiat", "500"), "Fiat 500X"),
    (("peugeot_208", "Peugeot", "208"), "Peugeot 2008"),
    (("peugeot_2008", "Peugeot", "2008"), "Peugeot 208"),
    (("volkswagen_id3", "Volkswagen", "ID.3"), "Volkswagen ID.4"),
    (("volkswagen_id4", "Volkswagen", "ID.4"), "Volkswagen ID.5"),
])
def test_fuzzy_never_crosses_designators(only_entry, query):
    """Score >= FUZZY_ACCEPT is not enough: with only one sibling in the
    dictionary, the other must stay unresolved instead of inheriting it."""
    key, make, model = only_entry
    d = ModelDictionary(models={key: _entry(make, model)}, aliases={})
    assert d.lookup(norm_key(query)) is None


def test_fuzzy_typo_with_matching_designator_still_accepted():
    d = ModelDictionary(models={"fiat_500": _entry("Fiat", "500")}, aliases={})
    m = d.lookup(norm_key("Fiaat 500"))  # make typo, ratio >= 0.90
    assert m is not None
    assert m.entry_id == "fiat_500"
    assert m.via == "fuzzy"


# ── §36 Hyundai i20 resolves to its own entry, never to the i10 ───────────────

@pytest.mark.parametrize("raw", [
    "Hyundai i20",
    "Hyundai I20",
    "HYUNDAI I20",
    "Hyundai i-20",
    "i20",
])
def test_i20_spellings_all_resolve(raw):
    r = classify_vehicle(VehicleInput(raw_model=raw))
    assert r.normalized.key == "hyundai_i20"
    assert r.normalized.make == "Hyundai"
    assert r.normalized.model == "i20"
    assert r.category.code == "E"
    assert r.type.code == "D"


def test_i20_manual_is_edmr():
    r = classify_vehicle(VehicleInput(raw_model="Hyundai i20", transmission="manual"))
    assert r.acriss == "EDMR"


def test_i20_automatic_is_edar():
    r = classify_vehicle(VehicleInput(raw_model="Hyundai i20", transmission="automatic"))
    assert r.acriss == "EDAR"


def test_i20_never_lands_on_i10():
    d = load_dictionary()
    m = d.lookup(norm_key("Hyundai i20"))
    assert m is not None and m.entry_id == "hyundai_i20"
    assert d.lookup(norm_key("Hyundai i10")).entry_id == "hyundai_i10"


# ── §36 Explicit aliases keep beating the guard (they resolve before fuzzy) ───

@pytest.mark.parametrize("raw,expected_key", [
    ("Volkswagen ID.4", "volkswagen_id4"),
    ("VW ID.4", "volkswagen_id4"),
    ("ID4", "volkswagen_id4"),
    ("ID 4", "volkswagen_id4"),
    ("Peugeot 208", "peugeot_208"),
    ("Peugeot 2008", "peugeot_2008"),
    ("Kia EV3", "kia_ev3"),
    ("Kia EV6", "kia_ev6"),
])
def test_designator_families_resolve_exactly(raw, expected_key):
    r = classify_vehicle(VehicleInput(raw_model=raw))
    assert r.normalized.key == expected_key
