"""Explicit powertrain-token detection on scraped text (§9).

Priority: PHEV > BEV > HYBRID > DIESEL > PETROL. PHEV beats a generic 'hybrid'
match by construction. Model-family knowledge (bev_only families like ID.3 or
Model 3) lives in the model dictionary, NOT here — this module only reads
explicit tokens. No single-letter matches (§9: never match a bare 'e').
"""
from __future__ import annotations

import re

from .normalizer import norm_key, strip_accents

# Word-bounded token groups, evaluated on the accent-stripped lowercase text.
_PHEV = (
    r"phev", r"plug in", r"plug in hybrid", r"plugin hybrid", r"recharge",
)
_BEV = (
    r"electric", r"electrico", r"electrica", r"electrique", r"elektro",
    r"bev", r"ev",
)
_HYBRID = (
    r"hybrid", r"hibrido", r"hibrida", r"hybride", r"hev", r"fhev", r"mhev",
    r"mild hybrid", r"e power", r"e hev",
)
_DIESEL = (
    r"diesel", r"tdi", r"tdci", r"dci", r"bluehdi", r"hdi", r"cdi", r"crdi",
)
_PETROL = (
    r"petrol", r"gasoline", r"gasolina", r"tsi", r"tfsi", r"puretech",
    r"mpi", r"essence",
)

# 'e-208' / 'e-2008' style: an 'e' prefix glued to a digit-bearing model name.
# Applied on the RAW (pre-key) text so the dash is still visible.
_E_PREFIX = re.compile(r"\be[- ]\d{3,4}\b", re.IGNORECASE)


def _has_any(key_padded: str, tokens: tuple[str, ...]) -> str | None:
    for tok in tokens:
        if f" {tok} " in key_padded:
            return tok
    return None


def detect_powertrain(raw_text: str) -> tuple[str, str] | None:
    """Return (fuel_code, matched_token) from explicit tokens, or None.

    fuel_code ∈ {'I','E','H','D','V'} (§7 normalization: PHEV→I, BEV→E,
    HEV/FHEV/MHEV→H, diesel→D, petrol→V).
    """
    key = f" {norm_key(raw_text)} "

    tok = _has_any(key, _PHEV)
    if tok:
        return "I", tok
    if _E_PREFIX.search(strip_accents(raw_text)):
        return "E", "e-<model> prefix"
    tok = _has_any(key, _BEV)
    if tok:
        return "E", tok
    tok = _has_any(key, _HYBRID)
    if tok:
        return "H", tok
    tok = _has_any(key, _DIESEL)
    if tok:
        return "D", tok
    tok = _has_any(key, _PETROL)
    if tok:
        return "V", tok
    return None
