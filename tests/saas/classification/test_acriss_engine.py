"""Mandatory scenarios for the deterministic ACRISS engine (spec §34).

Pure unit tests: no DB, no browser, no LLM.
"""
from __future__ import annotations

import pytest

from src.saas.application.classification.acriss_engine import (
    VehicleInput,
    classify_vehicle,
)


def clf(raw_model, transmission=None, doors=None, seats=None, source=None):
    return classify_vehicle(
        VehicleInput(
            raw_model=raw_model, transmission=transmission,
            doors=doors, seats=seats, source=source,
        )
    )


# ── §34 Compact manual ────────────────────────────────────────────────────────

def test_compact_manual_golf():
    r = clf("Volkswagen Golf", transmission="manual", doors=5, seats=5)
    assert r.category.code == "C"
    assert r.type.code == "D"
    assert r.transmission.code == "M"
    assert r.fuel_power.code == "R"
    assert r.transmission.confidence == 1.0


# ── §34 Compact automatic (token in the name) ────────────────────────────────

def test_compact_automatic_from_name_token():
    r = clf("VW Golf Automatic", transmission="automatic")
    assert r.partial_acriss.startswith("CD")
    assert r.transmission.code == "A"
    assert r.transmission.confidence == 1.0


# ── §34 Estate beats door count ───────────────────────────────────────────────

def test_estate_type_w_not_d():
    r = clf("Volkswagen Golf Variant", transmission="automatic", doors=5)
    assert r.type.code == "W"
    assert r.category.code == "C"


# ── §34 Crossover ─────────────────────────────────────────────────────────────

def test_crossover_t_cross():
    r = clf("Volkswagen T-Cross", transmission="automatic", doors=5, seats=5)
    assert r.category.code == "C"
    assert r.type.code == "G"
    assert r.transmission.code == "A"
    assert r.acriss == "CGAR"


# ── §34 Seven-seat SUV is NOT a passenger van ─────────────────────────────────

def test_seven_seat_suv_not_van():
    r = clf("Skoda Kodiaq", transmission="automatic", seats=7)
    assert r.type.code in ("F", "G")
    assert r.type.code != "V"
    assert r.category.code == "S"


# ── §34 Passenger van coding ──────────────────────────────────────────────────

def test_passenger_van_v_class():
    r = clf("Mercedes-Benz V-Class", transmission="automatic", seats=7)
    assert r.type.code == "V"
    # 7 seats + elite van → RV per §10 table
    assert r.category.code == "R"
    assert r.partial_acriss.startswith("RV")


def test_passenger_van_capacity_9_seats():
    r = clf("Toyota Proace Verso", transmission="manual", seats=9)
    assert r.partial_acriss.startswith("LV")
    assert r.acriss == "LVMR"


# ── §34 Electric ──────────────────────────────────────────────────────────────

def test_electric_tesla_model_3():
    r = clf("Tesla Model 3", transmission="automatic", doors=4, seats=5)
    assert r.fuel_power.code == "E"
    assert r.fuel_power.confidence >= 0.99


# ── §34 Hybrid token ──────────────────────────────────────────────────────────

def test_hybrid_token():
    r = clf("Toyota C-HR Hybrid", transmission="automatic", doors=5, seats=5)
    assert r.fuel_power.code == "H"


# ── §34 PHEV beats hybrid ─────────────────────────────────────────────────────

def test_phev_beats_hybrid():
    r = clf("Ford Kuga Plug-in Hybrid", transmission="automatic")
    assert r.fuel_power.code == "I"


# ── §34 Diesel token ──────────────────────────────────────────────────────────

def test_diesel_token():
    r = clf("Volkswagen Golf 2.0 TDI", transmission="automatic")
    assert r.fuel_power.code == "D"
    assert r.category.code == "C"


# ── §34 Known ICE without fuel token → R ──────────────────────────────────────

def test_known_ice_defaults_to_r():
    r = clf("SEAT Ibiza", transmission="manual")
    assert r.fuel_power.code == "R"
    assert r.fuel_power.confidence >= 0.90


# ── §34 AWD never inferred ────────────────────────────────────────────────────

def test_awd_never_inferred_for_suv():
    r = clf("Volkswagen Tiguan", transmission="automatic")
    assert r.transmission.code == "A"  # never B/D without explicit info


# ── §34 Ambiguous model: Qashqai ──────────────────────────────────────────────

def test_qashqai_reduced_confidence_and_alternative():
    r = clf("Nissan Qashqai", transmission="manual")
    assert r.acriss is not None
    assert r.type.confidence < 1.0
    assert r.needs_review is True
    assert any("F" == a.acriss[1] for a in r.alternatives if len(a.acriss) == 4)


# ── §11 Specific types beat door counts ───────────────────────────────────────

def test_convertible_beats_doors():
    r = clf("BMW 4 Series Convertible", transmission="automatic", doors=2)
    assert r.type.code == "T"


def test_roadster_mx5():
    r = clf("Mazda MX-5", transmission="manual", doors=2)
    assert r.type.code == "N"


# ── §17 Unknown never becomes X ───────────────────────────────────────────────

def test_unknown_model_never_x():
    r = clf("Wuling Bingo Plus", transmission="manual")
    assert r.category.code is None
    assert r.type.code != "X"
    assert r.partial_acriss[0] == "?"
    assert r.model_unresolved is True
    assert r.needs_review is True


# ── §15 Overall confidence = min ──────────────────────────────────────────────

def test_overall_confidence_is_min():
    r = clf("Nissan Qashqai", transmission="manual")
    letters = [r.category, r.type, r.transmission, r.fuel_power]
    assert r.confidence == min(l.confidence for l in letters)


# ── Mixed powertrain family without version → ambiguous, never fake certainty ─

def test_mixed_family_powertrain_ambiguous():
    r = clf("Volkswagen Golf", transmission="manual")
    # mixed family, no fuel token: best estimate R with honest confidence
    assert r.fuel_power.code == "R"
    assert r.fuel_power.confidence <= 0.95


# ── Output shape ──────────────────────────────────────────────────────────────

def test_result_serializes_to_dict():
    r = clf("Volkswagen T-Cross or similar", transmission="Automatic")
    d = r.to_dict()
    assert d["acriss"] == "CGAR"
    assert d["letters"]["type"]["source"] == "model_dictionary"
    assert d["explanation"]["transmission"].startswith("A from scraped")
    assert isinstance(d["assumptions"], list)
