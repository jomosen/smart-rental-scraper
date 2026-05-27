"""Compare LLM-extracted vehicles (truth) against DOM-extracted vehicles (selector path)."""
from __future__ import annotations

import json
from dataclasses import dataclass

from .models import VehicleResult

_PRICE_TOLERANCE = 0.02        # 2 cents tolerance for float price comparison
_MATCH_THRESHOLD = 0.90        # 90% field agreement required for match=True
# Fields that must agree at >=_MATCH_THRESHOLD to declare match=True.
# Secondary fields (seats, currency) are reported in field_agreement but do
# not block match.
_KEY_FIELDS = ("model", "group_code", "price_final", "transmission")


@dataclass
class VerificationResult:
    match: bool
    llm_count: int
    dom_count: int
    field_agreement: dict[str, float]   # field → fraction in [0,1]
    mismatches: list[str]
    rationale: str


# ── Matching helpers ──────────────────────────────────────────────────────────

def _normalize_str(v: str | None) -> str:
    return (v or "").strip().upper()


def _vehicles_same_identity(a: VehicleResult, b: VehicleResult) -> bool:
    """Two vehicles share identity if model + group_code match (case-insensitive)."""
    ma, mb = _normalize_str(a.model), _normalize_str(b.model)
    ga, gb = _normalize_str(a.group_code), _normalize_str(b.group_code)
    if not ma or not mb:
        return False
    if ma != mb:
        return False
    # If both have group codes, they must match
    if ga and gb:
        return ga == gb
    # If only one has a group code, treat as match (partial info)
    return True


def _match_lists(
    llm: list[VehicleResult],
    dom: list[VehicleResult],
) -> list[tuple[VehicleResult, VehicleResult | None]]:
    """Pair each LLM vehicle with the best DOM vehicle by model+group_code."""
    remaining = list(dom)
    pairs: list[tuple[VehicleResult, VehicleResult | None]] = []
    for lv in llm:
        matched = None
        for dv in remaining:
            if _vehicles_same_identity(lv, dv):
                matched = dv
                remaining.remove(dv)
                break
        pairs.append((lv, matched))
    return pairs


def _fields_agree(field: str, a_val, b_val) -> bool:
    if a_val is None and b_val is None:
        return True
    if a_val is None or b_val is None:
        return False
    if isinstance(a_val, float) and isinstance(b_val, float):
        return abs(a_val - b_val) <= _PRICE_TOLERANCE
    if isinstance(a_val, int) and isinstance(b_val, int):
        return a_val == b_val
    return _normalize_str(str(a_val)) == _normalize_str(str(b_val))


_ALL_FIELDS = (
    "model", "group_code", "transmission",
    "seats", "price_final", "currency",
)


# ── Public function ───────────────────────────────────────────────────────────

def verify(
    llm_vehicles: list[VehicleResult],
    dom_vehicles: list[VehicleResult],
) -> VerificationResult:
    """
    Compare the two extraction paths. Pure function, no I/O.

    match=True when:
      - Same vehicle count.
      - All key fields (model, group_code, price_final) agree at ≥ _MATCH_THRESHOLD.
    """
    if not llm_vehicles:
        return VerificationResult(
            match=False,
            llm_count=0, dom_count=len(dom_vehicles),
            field_agreement={},
            mismatches=["LLM extraction returned 0 vehicles"],
            rationale="No reference vehicles to compare against",
        )

    pairs = _match_lists(llm_vehicles, dom_vehicles)
    mismatches: list[str] = []

    # Count agreement per field (only over matched pairs)
    agree: dict[str, int] = {f: 0 for f in _ALL_FIELDS}
    compared: dict[str, int] = {f: 0 for f in _ALL_FIELDS}

    for lv, dv in pairs:
        if dv is None:
            mismatches.append(
                f"No DOM match for LLM vehicle: model={lv.model!r} group={lv.group_code!r}"
            )
            continue
        for field in _ALL_FIELDS:
            a_val = getattr(lv, field)
            b_val = getattr(dv, field)
            if a_val is None and b_val is None:
                continue
            compared[field] += 1
            if _fields_agree(field, a_val, b_val):
                agree[field] += 1
            else:
                mismatches.append(
                    f"{lv.model}/{lv.group_code} — {field}: "
                    f"llm={a_val!r} dom={b_val!r}"
                )

    field_agreement = {
        f: (agree[f] / compared[f]) if compared[f] > 0 else 1.0
        for f in _ALL_FIELDS
    }

    count_ok = len(llm_vehicles) == len(dom_vehicles)
    if not count_ok:
        mismatches.insert(0,
            f"Count mismatch: llm={len(llm_vehicles)} dom={len(dom_vehicles)}")

    key_agreement_ok = all(
        field_agreement.get(f, 0.0) >= _MATCH_THRESHOLD
        for f in _KEY_FIELDS
    )
    match = count_ok and key_agreement_ok

    if match:
        rationale = (
            f"All {len(llm_vehicles)} vehicles matched with "
            f"≥{_MATCH_THRESHOLD:.0%} agreement on key fields."
        )
    else:
        failing = [f for f in _KEY_FIELDS if field_agreement.get(f, 0) < _MATCH_THRESHOLD]
        rationale = (
            f"Match failed. count_ok={count_ok}. "
            f"Low-agreement key fields: {failing}. "
            f"Mismatches: {len(mismatches)}."
        )

    return VerificationResult(
        match=match,
        llm_count=len(llm_vehicles),
        dom_count=len(dom_vehicles),
        field_agreement=field_agreement,
        mismatches=mismatches,
        rationale=rationale,
    )
