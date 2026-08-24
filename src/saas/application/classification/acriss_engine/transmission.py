"""Third ACRISS letter — transmission (§6).

Only scraped/explicit information is used. NEVER infer AWD/4WD from body type:
without explicit drive info the letter is M or A (unspecified drive).
"""
from __future__ import annotations

import re

from .normalizer import norm_key

_AUTO_TOKENS = {
    "automatic", "automatico", "automatica", "automatique", "automatik",
    "auto", "at", "dsg", "dct", "edc", "eat", "cvt", "e cvt", "ecvt",
    "steptronic", "tiptronic", "s tronic", "stronic",
}
_MANUAL_TOKENS = {
    "manual", "manuale", "manuel", "manuell", "mt", "stick", "schaltgetriebe",
}


def _classify_text(text: str | None) -> str | None:
    if not text:
        return None
    key = f" {norm_key(text)} "
    # multi-word tokens first
    for tok in ("e cvt", "s tronic"):
        if f" {tok} " in key:
            return "A"
    for tok in _AUTO_TOKENS:
        if f" {tok} " in key:
            return "A"
    for tok in _MANUAL_TOKENS:
        if f" {tok} " in key:
            return "M"
    return None


def resolve_transmission(
    scraped: str | None, raw_model: str
) -> tuple[str | None, float, str, str]:
    """Return (code, confidence, source, explanation).

    Priority: scraped field (confidence 1.0) → token in the model name (0.98)
    → None (partial letter; caller decides fallback).
    """
    code = _classify_text(scraped)
    if code is not None:
        return code, 1.0, "scraped", f"{code} from scraped transmission {scraped!r}"

    code = _classify_text(raw_model)
    if code is not None:
        return code, 0.98, "name_token", f"{code} from token in model name"

    return None, 0.0, "fallback", "transmission unknown (no scraped value, no token)"


_SINGLE_SPEED_EV = re.compile(r"\bsingle\s*speed\b", re.IGNORECASE)


def is_single_speed_ev(text: str | None) -> bool:
    return bool(text and _SINGLE_SPEED_EV.search(text))
