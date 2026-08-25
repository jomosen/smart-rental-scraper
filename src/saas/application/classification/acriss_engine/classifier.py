"""classifyVehicle() — deterministic ACRISS pipeline (spec §30).

RAW → normalize → source override / model dictionary → deterministic rules →
fallbacks → confidence + alternatives. No LLM anywhere in this module: unknown
models come back with `model_unresolved=True` and partial letters; the caller
may route them to the semantic LLM resolver and the review queue (§31-32).

Non-negotiable rules honored here (§41): premium brand never implies Premium
category; SUV never implies AWD; 7 seats never implies Passenger Van alone;
unknown never becomes X; the engine, not any LLM, builds the code.
"""
from __future__ import annotations

import re
from typing import Optional

from .codes import (
    CATEGORY_NAMES,
    FUEL_NAMES,
    TRANSMISSION_NAMES,
    TYPE_NAMES,
    passenger_van_first_letter,
)
from .dictionaries import (
    ModelDictionary,
    ModelMatch,
    find_source_override,
    load_dictionary,
    load_source_overrides,
)
from .normalizer import detect_make, normalize_for_matching
from .powertrain import detect_powertrain
from .transmission import resolve_transmission
from .types import (
    AcrissResult,
    AlternativeResult,
    LetterResult,
    NormalizedModel,
    VehicleInput,
)

# Body-variant tokens STRONG enough to beat the dictionary body type when the
# matched entry's own name does not carry them (§11 examples: "4 Series
# Convertible" → T even if the family entry says coupe). Descriptors like
# "SUV"/"crossover" are WEAK: the dictionary wins over them (§12: "Qashqai SUV"
# is still the dictionary's Qashqai).
_STRONG_TYPE_TOKENS: tuple[tuple[str, str], ...] = (
    ("convertible", "T"), ("cabrio", "T"), ("cabriolet", "T"), ("descapotable", "T"),
    ("variant", "W"), ("estate", "W"), ("sw", "W"), ("touring", "W"),
    ("kombi", "W"), ("avant", "W"), ("break", "W"), ("sportstourer", "W"),
    ("roadster", "N"),
    ("coupe", "E"),
    ("furgon", "K"), ("furgoneta", "K"), ("cargo", "K"),
)
_WEAK_TYPE_TOKENS: tuple[tuple[str, str], ...] = (
    ("crossover", "G"), ("cuv", "G"), ("suv", "F"),
    ("monovolumen", "M"), ("mpv", "M"), ("monospace", "M"),
    ("sedan", "D"), ("berlina", "D"), ("saloon", "D"),
)


def _token_type(text: str, tokens: tuple[tuple[str, str], ...]) -> Optional[tuple[str, str]]:
    padded = f" {text} "
    for tok, code in tokens:
        if f" {tok} " in padded:
            return code, tok
    return None


def _letter(code, name_table, confidence, source, explanation) -> LetterResult:
    return LetterResult(
        code=code,
        name=name_table.get(code) if code else None,
        confidence=confidence,
        source=source,
        explanation=explanation,
    )


def classify_vehicle(
    vehicle: VehicleInput,
    dictionary: Optional[ModelDictionary] = None,
    overrides: Optional[dict] = None,
) -> AcrissResult:
    dictionary = dictionary if dictionary is not None else load_dictionary()
    overrides = overrides if overrides is not None else load_source_overrides()

    assumptions: list[str] = []
    alternatives: list[AlternativeResult] = []
    powertrain_ambiguous = False

    # ── Normalization + model resolution ─────────────────────────────────────
    norm_text = normalize_for_matching(vehicle.raw_model)
    match: Optional[ModelMatch] = dictionary.lookup(norm_text)
    entry = match.entry if match else None
    entry_id = match.entry_id if match else None

    make, _rest = detect_make(norm_text)
    if entry:
        normalized = NormalizedModel(
            make=entry["make"], model=entry["model"], variant=None, key=entry_id
        )
    else:
        normalized = NormalizedModel(make=make, model=_rest or None, variant=None, key=None)

    if match and match.via == "fuzzy":
        assumptions.append(
            f"Model resolved by fuzzy match (score {match.score:.2f}) — verify"
        )

    override = find_source_override(overrides, vehicle.source, entry_id)
    verified = bool(entry and entry.get("verification") == "verified")

    # ── Explicit powertrain tokens (§9) — read before any fallback ───────────
    pt = detect_powertrain(vehicle.raw_model)

    # ── Passenger Van determination (§10, §28) ────────────────────────────────
    is_van_family = bool(entry and entry.get("van"))
    van_coding = False
    if is_van_family:
        if vehicle.seats is not None and vehicle.seats >= 6:
            van_coding = True
        elif vehicle.seats is None:
            van_coding = True  # family-known van without seats: capacity uncertain

    # ── First two letters ─────────────────────────────────────────────────────
    if van_coding:
        elite = bool(entry.get("van_elite"))
        if vehicle.seats is not None:
            first = passenger_van_first_letter(vehicle.seats, elite)
            category = _letter(
                first, CATEGORY_NAMES, 0.95, "model_dictionary",
                f"{first} from passenger-van capacity table ({vehicle.seats} seats)",
            )
        else:
            first = entry.get("van_default_first", "S")
            conf = 0.60 if "van_default_first" not in entry else 0.85
            category = _letter(
                first, CATEGORY_NAMES, conf, "model_dictionary",
                f"{first} passenger-van default (seats unknown)",
            )
            assumptions.append("Passenger van capacity unknown — seats not scraped")
        veh_type = _letter(
            "V", TYPE_NAMES, entry.get("body", {}).get("confidence", 0.95),
            "model_dictionary", "V passenger van from model family",
        )
    else:
        category = _resolve_category(override, entry, match)
        veh_type = _resolve_type(override, entry, match, norm_text, vehicle, assumptions)

        # SUV/Crossover (or other) declared alternative on the entry (§5, §29)
        if entry and not override and entry.get("type_alternative"):
            alt = entry["type_alternative"]
            if veh_type.code and alt.get("type") and alt["type"] != veh_type.code:
                alternatives.append(AlternativeResult(
                    acriss="", confidence=float(alt.get("confidence", 0.7)),
                    reason=alt.get("reason", "alternative body type per dictionary"),
                ))
                # placeholder acriss filled in after all letters resolve

    # ── Transmission (§6) ─────────────────────────────────────────────────────
    t_code, t_conf, t_source, t_expl = resolve_transmission(
        vehicle.transmission, vehicle.raw_model
    )
    if t_code is None and pt and pt[0] == "E":
        # BEVs are single-speed: automatic by construction.
        t_code, t_conf, t_source = "A", 0.97, "heuristic"
        t_expl = "A because the vehicle is electric (single-speed)"
    transmission = _letter(t_code, TRANSMISSION_NAMES, t_conf, t_source, t_expl)
    if t_code in ("M", "A") and t_source in ("scraped", "name_token"):
        assumptions.append("Drive type not specified — unspecified-drive letter used")

    # ── Fuel / power (§7-§9, §19) ─────────────────────────────────────────────
    fuel, fuel_ambiguous, fuel_assumptions, fuel_alt = _resolve_fuel(pt, entry)
    powertrain_ambiguous = powertrain_ambiguous or fuel_ambiguous
    assumptions.extend(fuel_assumptions)

    # BEV single-speed rule, family edition: a bev_only family without any
    # transmission signal is automatic by construction, same as the token path.
    if transmission.code is None and fuel.code == "E":
        transmission = _letter(
            "A", TRANSMISSION_NAMES, 0.97, "heuristic",
            "A because the vehicle is electric (single-speed)",
        )

    # ── Assemble ──────────────────────────────────────────────────────────────
    letters = [category, veh_type, transmission, fuel]
    partial = "".join(lr.code if lr.code else "?" for lr in letters)
    acriss = partial if "?" not in partial else None
    confidence = min(lr.confidence for lr in letters)

    # Materialize alternatives now that all letters are known
    final_alts: list[AlternativeResult] = []
    for alt in alternatives:
        if not alt.acriss and entry and entry.get("type_alternative"):
            alt_type = entry["type_alternative"]["type"]
            alt_code = (
                (category.code or "?") + alt_type
                + (transmission.code or "?") + (fuel.code or "?")
            )
            final_alts.append(AlternativeResult(alt_code, alt.confidence, alt.reason))
        elif alt.acriss:
            final_alts.append(alt)
    if fuel_alt and acriss:
        final_alts.append(AlternativeResult(
            acriss[:3] + fuel_alt[0], fuel_alt[1], fuel_alt[2]
        ))

    model_unresolved = entry is None
    needs_review = (
        confidence < 0.85
        or category.confidence < 0.85
        or veh_type.confidence < 0.85
        or model_unresolved
        or (powertrain_ambiguous and fuel.confidence < 0.85)
    )

    # classification_source (§0)
    sources = {lr.source for lr in letters}
    if override:
        classification_source = "source_override"
    elif sources <= {"model_dictionary", "scraped"}:
        classification_source = "official_verified" if verified else "model_dictionary"
    elif "model_dictionary" in sources or "scraped" in sources:
        classification_source = "mixed"
    else:
        classification_source = "heuristic"

    return AcrissResult(
        raw_model=vehicle.raw_model,
        normalized=normalized,
        acriss=acriss,
        partial_acriss=partial,
        confidence=confidence,
        category=category,
        type=veh_type,
        transmission=transmission,
        fuel_power=fuel,
        classification_source=classification_source,
        assumptions=assumptions,
        alternatives=final_alts,
        powertrain_ambiguous=powertrain_ambiguous,
        needs_review=needs_review,
        model_unresolved=model_unresolved,
    )


def classify_vehicles(
    vehicles: list[VehicleInput],
    dictionary: Optional[ModelDictionary] = None,
    overrides: Optional[dict] = None,
) -> list[AcrissResult]:
    """Batch API (§40). Pure dictionary/rules — no per-vehicle LLM calls.
    Unknowns are flagged via `model_unresolved` for the caller to group and
    resolve through whatever costly fallback exists."""
    dictionary = dictionary if dictionary is not None else load_dictionary()
    overrides = overrides if overrides is not None else load_source_overrides()
    return [classify_vehicle(v, dictionary, overrides) for v in vehicles]


# ── Letter resolvers ──────────────────────────────────────────────────────────

def _resolve_category(
    override: Optional[dict], entry: Optional[dict], match: Optional[ModelMatch]
) -> LetterResult:
    if override and override.get("category"):
        return _letter(
            override["category"], CATEGORY_NAMES,
            float(override.get("confidence", 0.99)), "source_override",
            f"{override['category']} from source override",
        )
    if entry:
        cat = entry.get("category", {})
        conf = float(cat.get("confidence", 0.90))
        if match and match.via == "fuzzy":
            conf = min(conf, 0.88)
        return _letter(
            cat.get("code"), CATEGORY_NAMES, conf, "model_dictionary",
            f"{cat.get('code')} from model dictionary {entry['make']} {entry['model']}",
        )
    # Unknown model: NEVER X (§17); leave the letter open for the semantic
    # fallback — partial_acriss renders '?'.
    return _letter(None, CATEGORY_NAMES, 0.0, "fallback",
                   "category unknown — model not in dictionary")


def _resolve_type(
    override: Optional[dict],
    entry: Optional[dict],
    match: Optional[ModelMatch],
    norm_text: str,
    vehicle: VehicleInput,
    assumptions: list[str],
) -> LetterResult:
    """Priority (§11): source_override → strong name token not reflected in the
    matched entry → dictionary body → weak name token → doors → open."""
    if override and override.get("type"):
        return _letter(
            override["type"], TYPE_NAMES,
            float(override.get("confidence", 0.99)), "source_override",
            f"{override['type']} from source override",
        )

    strong = _token_type(norm_text, _STRONG_TYPE_TOKENS)
    if strong:
        code, tok = strong
        entry_name = f"{entry['make']} {entry['model']}".lower() if entry else ""
        if not entry or tok not in entry_name:
            return _letter(code, TYPE_NAMES, 0.98, "name_token",
                           f"{code} from body token {tok!r} in the name")

    if entry:
        body = entry.get("body", {})
        conf = float(body.get("confidence", 0.90))
        if match and match.via == "fuzzy":
            conf = min(conf, 0.88)
        return _letter(body.get("type"), TYPE_NAMES, conf, "model_dictionary",
                       f"{body.get('type')} from model dictionary body type")

    weak = _token_type(norm_text, _WEAK_TYPE_TOKENS)
    if weak:
        code, tok = weak
        return _letter(code, TYPE_NAMES, 0.75, "name_token",
                       f"{code} from descriptor {tok!r} (no dictionary entry)")

    if vehicle.doors is not None:
        if vehicle.doors <= 3:
            return _letter("B", TYPE_NAMES, 0.70, "heuristic",
                           f"B from {vehicle.doors} doors (no better source)")
        assumptions.append("Body type inferred from door count only")
        return _letter("D", TYPE_NAMES, 0.70, "heuristic",
                       f"D from {vehicle.doors} doors (no better source)")

    return _letter(None, TYPE_NAMES, 0.0, "fallback",
                   "body type unknown — no dictionary entry, token or doors")


def _resolve_fuel(
    pt: Optional[tuple[str, str]], entry: Optional[dict]
) -> tuple[LetterResult, bool, list[str], Optional[tuple[str, float, str]]]:
    """Returns (letter, powertrain_ambiguous, assumptions, alt_fuel).

    alt_fuel = (code, confidence, reason) to surface as an alternative when the
    family is mixed (§8)."""
    if pt:
        code, token = pt
        return (
            _letter(code, FUEL_NAMES, 0.99, "name_token",
                    f"{code} from explicit powertrain token {token!r}"),
            False, [], None,
        )

    if entry:
        profile = entry.get("powertrain_profile", {})
        mode = profile.get("mode", "mixed")
        default = profile.get("default_code", "R")
        conf = float(profile.get("confidence", 0.80))
        if mode == "bev_only":
            return (
                _letter("E", FUEL_NAMES, max(conf, 0.99), "model_dictionary",
                        "E — family is electric-only"),
                False, [], None,
            )
        if mode == "hybrid_only":
            return (
                _letter("H", FUEL_NAMES, conf, "model_dictionary",
                        "H — family is hybrid-only"),
                False, [], None,
            )
        if mode == "phev_only":
            return (
                _letter("I", FUEL_NAMES, conf, "model_dictionary",
                        "I — family is plug-in-hybrid-only"),
                False, [], None,
            )
        if mode == "ice_only":
            return (
                _letter("R", FUEL_NAMES, max(conf, 0.95), "model_dictionary",
                        "R — combustion-only family, fuel unspecified"),
                False,
                ["No explicit fuel information was scraped"],
                None,
            )
        if mode == "ice_dominant":
            return (
                _letter("R", FUEL_NAMES, min(max(conf, 0.90), 0.95), "fallback",
                        "R — ICE-dominant family, no fuel token found"),
                False,
                ["No explicit fuel information was scraped",
                 "Vehicle treated as combustion-engine family"],
                None,
            )
        # mixed: best estimate + explicit ambiguity (§8)
        return (
            _letter(default, FUEL_NAMES, min(conf, 0.85), "fallback",
                    f"{default} — family sold with several powertrains, none identified"),
            True,
            ["Family offers several powertrains; scraped name does not identify one"],
            ("H", min(conf, 0.85) - 0.05, "family also sold as hybrid") if default == "R" else None,
        )

    # Unknown model, no token: looks-ICE fallback with reduced confidence (§31.7)
    return (
        _letter("R", FUEL_NAMES, 0.70, "fallback",
                "R — unknown model, no powertrain token; assumed combustion"),
        True,
        ["Unknown model — combustion assumed without evidence"],
        None,
    )
