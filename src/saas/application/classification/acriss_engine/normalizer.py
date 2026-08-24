"""normalizeVehicle() — spec §12: noise stripping, brand canonicalization,
accent/case/dash-insensitive matching keys. raw_model is never mutated in the
output; normalization only feeds matching.
"""
from __future__ import annotations

import re
import unicodedata

# Phrases that carry no vehicle identity (§12). Word-bounded, case-insensitive,
# matched on the accent-stripped text.
_NOISE_PHRASES = (
    "or similar", "o similar", "ou similaire", "oder ahnlich", "o simile",
    "ou similar", "category", "class", "grupo", "group",
)

# Brand canonicalization (§12). Keys are normalized tokens.
_BRAND_ALIASES = {
    "vw": "Volkswagen",
    "mb": "Mercedes-Benz",
    "mercedes": "Mercedes-Benz",
    "merc": "Mercedes-Benz",
    "mercedes benz": "Mercedes-Benz",
    "citroen": "Citroën",
    "skoda": "Škoda",
}

_KNOWN_MAKES = (
    # normalized-token → display. Extend as the dictionary grows.
    ("volkswagen", "Volkswagen"),
    ("mercedes benz", "Mercedes-Benz"),
    ("alfa romeo", "Alfa Romeo"),
    ("land rover", "Land Rover"),
    ("skoda", "Škoda"),
    ("citroen", "Citroën"),
    ("audi", "Audi"), ("bmw", "BMW"), ("seat", "SEAT"), ("cupra", "Cupra"),
    ("peugeot", "Peugeot"), ("renault", "Renault"), ("opel", "Opel"),
    ("ford", "Ford"), ("toyota", "Toyota"), ("kia", "Kia"),
    ("hyundai", "Hyundai"), ("nissan", "Nissan"), ("fiat", "Fiat"),
    ("dacia", "Dacia"), ("tesla", "Tesla"), ("volvo", "Volvo"),
    ("mazda", "Mazda"), ("jaguar", "Jaguar"), ("lexus", "Lexus"),
    ("mini", "Mini"), ("ds", "DS"), ("porsche", "Porsche"),
    ("mg", "MG"), ("omoda", "Omoda"), ("jaecoo", "Jaecoo"), ("ebro", "Ebro"),
    ("honda", "Honda"), ("suzuki", "Suzuki"), ("jeep", "Jeep"),
    ("piaggio", "Piaggio"),
)


def strip_accents(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFD", text)
        if unicodedata.category(ch) != "Mn"
    )


def norm_key(text: str) -> str:
    """Matching key: accent-stripped, lowercased, punctuation → space,
    collapsed whitespace. 'T-Cross' / 'T Cross' / 'TCross'... note that
    dash removal keeps 'tcross' and 't cross' DISTINCT keys — alias entries
    cover the variants (§12 examples)."""
    text = strip_accents(text).lower()
    text = re.sub(r"[\-_/.,:;()\[\]+!*']", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def strip_noise(text: str) -> str:
    """Remove 'or similar'-style phrases (word-bounded) from a raw string.

    The single-word noise terms (class/category/group/grupo) are NOT stripped
    when preceded by a single letter ("V-Class", "C Class") — there they are
    part of a model name, not rental-listing noise.
    """
    out = strip_accents(text)
    for phrase in _NOISE_PHRASES:
        if " " in phrase:
            out = re.sub(rf"\b{re.escape(phrase)}\b", " ", out, flags=re.IGNORECASE)
        else:
            out = re.sub(
                rf"(?<![A-Za-z]-)(?<![A-Za-z] )\b{re.escape(phrase)}\b",
                " ", out, flags=re.IGNORECASE,
            )
    return re.sub(r"\s+", " ", out).strip()


def canonical_brand(token_text: str) -> str | None:
    """Return the canonical brand display name for a normalized token string."""
    return _BRAND_ALIASES.get(token_text)


def detect_make(normalized_text: str) -> tuple[str | None, str]:
    """Find a known make at any position; return (display_make, text_without_make).

    Brand aliases are resolved first (e.g. 'vw golf' → Volkswagen + 'golf').
    """
    text = f" {normalized_text} "
    # Longest alias/make first, so "mercedes benz" wins over "mercedes".
    candidates = sorted(
        list(_BRAND_ALIASES.items()) + list(_KNOWN_MAKES),
        key=lambda kv: len(kv[0]), reverse=True,
    )
    for token, display in candidates:
        needle = f" {token} "
        if needle in text:
            remainder = text.replace(needle, " ", 1)
            return display, re.sub(r"\s+", " ", remainder).strip()
    return None, normalized_text


def normalize_for_matching(raw_model: str) -> str:
    """Full pipeline raw → matching text: noise stripping + key normalization."""
    return norm_key(strip_noise(raw_model))
