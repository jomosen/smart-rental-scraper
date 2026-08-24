"""Normalization + fuzzy-matching guards (spec §35-§36)."""
from __future__ import annotations

import pytest

from src.saas.application.classification.acriss_engine import (
    VehicleInput,
    classify_vehicle,
    load_dictionary,
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
