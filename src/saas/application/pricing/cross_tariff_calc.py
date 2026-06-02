"""Pure calculation logic for the cross-provider tariff view.

Pipeline per cell (acriss_code × duration × zone):

  1. Colapso intra-proveedor  — min(total) when a provider has several groups
                                for the same ACRISS code.
  2. Descarte de anomalías    — discard a provider whose total is far from
                                the others (cross-corroboration; requires ≥ 2
                                other providers as witnesses).  Cell is marked
                                `degraded` when at least one provider is dropped
                                for anomaly.
  3. Agregación inter-proveedor — min / med / avg / max over remaining totals.
  4. Regla                    — base ± (val% or val€).
  5. Clamp                    — floor / ceiling circuit breakers.
  6. Redondeo direccional     — configurable rounding with guard: the rounding
                                must never invert the direction of the rule.

The TOTAL is the source of truth throughout.  €/day is derived (total/duration)
and returned for display only — it never feeds back into the calculation.

No I/O here: callers pass plain Python structures; this module has no imports
from infrastructure or presentation layers.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

# ── Anomaly thresholds ──────────────────────────────────────────────────────
# A provider's total is an outlier if it deviates by more than this ratio from
# the median of the *other* providers, provided ≥ 2 witnesses exist.
# These are conservative by design: better to miss a genuine outlier than to
# discard a legitimately cheap / expensive provider.
_ANOMALY_LOW_RATIO = Decimal("0.40")   # < 40 % of others' median → too cheap
_ANOMALY_HIGH_RATIO = Decimal("2.50")  # > 250 % of others' median → too expensive


# ── Public data structures ──────────────────────────────────────────────────

@dataclass
class RuleConfig:
    op: str              # "sub" | "add"
    val: Decimal         # absolute amount or percentage
    mode: str            # "pct" | "abs"
    floor_mode: str      # "auto" | "cost" | "none"
    ceiling_mode: str    # "max" | "none"


@dataclass
class ProviderContribution:
    provider_code: str
    total: Decimal
    per_day: Decimal
    is_base: bool = False       # True for the provider that determined base
    stale: bool = False
    stale_days: int = 0


@dataclass
class DroppedProvider:
    provider_code: str
    total: Optional[Decimal]
    reason: str                 # "anomaly" | "missing" | "stale_excluded"


@dataclass
class CellFlags:
    coverage: int               # providers with valid data
    total_providers: int        # providers requested
    clamped: Optional[str]      # "floor" | "ceiling" | None
    stale: bool
    degraded: bool              # ≥1 anomaly dropped
    inferred: bool              # price from zone representative, not today


@dataclass
class CellResult:
    acriss_code: str
    duration: int
    rec_total: Optional[Decimal]       # final recommended price (total €)
    rec_per_day: Optional[Decimal]     # derived display value
    base_total: Optional[Decimal]      # aggregate before rule
    adjusted_total: Optional[Decimal]  # after rule, before clamp
    clamped_total: Optional[Decimal]   # after clamp
    floor_val: Optional[Decimal]
    ceiling_val: Optional[Decimal]
    present: list[ProviderContribution] = field(default_factory=list)
    dropped: list[DroppedProvider] = field(default_factory=list)
    flags: CellFlags = field(default_factory=lambda: CellFlags(0, 0, None, False, False, True))
    rule_used: Optional[RuleConfig] = None
    rule_is_default: bool = True
    round_flip: bool = False    # True when rounding guard adjusted the value


# ── Step 1 — Intra-provider collapse ───────────────────────────────────────

def collapse_intra_provider(
    provider_totals: dict[str, list[Decimal]],
) -> dict[str, Decimal]:
    """Return min total per provider; handles providers with multiple groups (e.g. Centauro).

    Input:  {provider_code: [total1, total2, ...]}  — totals for all groups
    Output: {provider_code: min_total}
    """
    return {pk: min(totals) for pk, totals in provider_totals.items() if totals}


# ── Step 2 — Anomaly detection ──────────────────────────────────────────────

def detect_anomalies(
    collapsed: dict[str, Decimal],
) -> dict[str, bool]:
    """Return {provider_code: is_anomaly}.

    Requires ≥ 2 *other* providers as witnesses before marking any outlier.
    With only 1 or 2 total providers no anomaly is ever flagged (insufficient
    corroboration — we cannot tell which side is wrong).
    """
    if len(collapsed) < 3:
        return {pk: False for pk in collapsed}

    result: dict[str, bool] = {}
    for pk, val in collapsed.items():
        others = [float(v) for p, v in collapsed.items() if p != pk]
        med = Decimal(str(statistics.median(others)))
        if med == 0:
            result[pk] = False
            continue
        ratio = val / med
        result[pk] = ratio < _ANOMALY_LOW_RATIO or ratio > _ANOMALY_HIGH_RATIO
    return result


# ── Step 3 — Inter-provider aggregation ─────────────────────────────────────

def aggregate_cross_provider(
    valid_totals: list[Decimal],
    base: str,
) -> Optional[Decimal]:
    """Aggregate over provider totals.  Returns None when the list is empty.

    base: "min" | "med" | "avg" | "max"
    """
    if not valid_totals:
        return None
    s = sorted(valid_totals)
    if base == "min":
        return s[0]
    if base == "max":
        return s[-1]
    if base == "avg":
        return Decimal(str(sum(float(v) for v in s) / len(s)))
    if base == "med":
        n = len(s)
        mid = n // 2
        return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2
    raise ValueError(f"Unknown base aggregation: {base!r}")


# ── Step 4 — Rule application ────────────────────────────────────────────────

def apply_rule(base_total: Decimal, rule: RuleConfig) -> Decimal:
    """Apply ± rule to the base total."""
    sign = Decimal("-1") if rule.op == "sub" else Decimal("1")
    if rule.mode == "pct":
        return base_total * (1 + sign * rule.val / 100)
    return base_total + sign * rule.val


# ── Step 5 — Clamp ──────────────────────────────────────────────────────────

def apply_clamp(
    adjusted: Decimal,
    floor_val: Optional[Decimal],
    ceiling_val: Optional[Decimal],
) -> tuple[Decimal, Optional[str]]:
    """Apply floor/ceiling circuit breakers.

    Returns (clamped_value, which_clamp_fired) where which_clamp_fired is
    "floor", "ceiling", or None.
    """
    clamped = adjusted
    fired: Optional[str] = None
    if floor_val is not None and clamped < floor_val:
        clamped = floor_val
        fired = "floor"
    if ceiling_val is not None and clamped > ceiling_val:
        clamped = ceiling_val
        fired = "ceiling"
    return clamped, fired


def compute_floor_ceiling(
    valid_totals: list[Decimal],
    rule: RuleConfig,
) -> tuple[Optional[Decimal], Optional[Decimal]]:
    """Derive floor and ceiling values from the rule config and market data.

    floor_mode "auto": min_of_market × 0.82 — placeholder for V0.
    # TODO (D5): the floor must NOT be derived from the same data it protects.
    #   A poisoned scrape would lower the floor exactly when you need it most.
    #   Replace "auto" with a percentile of own historical data or cost+margin.
    floor_mode "cost": same placeholder (0.55 × max) — wire up real cost model later.
    floor_mode "none": no floor.
    ceiling_mode "max": max of market.
    ceiling_mode "none": no ceiling.
    """
    if not valid_totals:
        return None, None

    floor_val: Optional[Decimal] = None
    if rule.floor_mode == "auto":
        floor_val = min(valid_totals) * Decimal("0.82")
    elif rule.floor_mode == "cost":
        # TODO (D5): replace with real cost+margin model.
        floor_val = max(valid_totals) * Decimal("0.55")
    # "none" → floor_val stays None

    ceiling_val: Optional[Decimal] = None
    if rule.ceiling_mode == "max":
        ceiling_val = max(valid_totals)
    # "none" → ceiling_val stays None

    return floor_val, ceiling_val


# ── Step 6 — Directional rounding with guard ────────────────────────────────

_ROUND_STEPS = {
    "0":    None,
    "1":    Decimal("1"),
    "0.50": Decimal("0.5"),
    "0.99": Decimal("1"),    # target ,99 cents  (step = 1 for guard purposes)
    "0.90": Decimal("1"),    # target ,90 cents
}

_ROUND_OFFSETS = {
    "0":    Decimal("0"),
    "1":    Decimal("0"),
    "0.50": Decimal("0"),
    "0.99": Decimal("0.99"),
    "0.90": Decimal("0.90"),
}


def apply_rounding_directional(
    value: Decimal,
    base_total: Decimal,
    rule: RuleConfig,
    round_mode: str,
) -> tuple[Decimal, bool]:
    """Round value and apply the directional guard.

    Invariant: if rule.op == "sub", result < base_total.
               if rule.op == "add", result > base_total.
    When rounding would violate the invariant the value is nudged by one step
    in the correct direction.

    Returns (rounded_value, round_flip_occurred).
    """
    if round_mode == "0" or rule.val == 0:
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), False

    step = _ROUND_STEPS[round_mode]
    offset = _ROUND_OFFSETS[round_mode]

    if step is None:
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), False

    # Round to nearest ,XX
    base_int = (value - offset).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    rounded = base_int + offset

    flip = False
    if rule.op == "sub" and rounded >= base_total:
        while rounded >= base_total:
            rounded -= step
        flip = True
    elif rule.op == "add" and rounded <= base_total:
        while rounded <= base_total:
            rounded += step
        flip = True

    return rounded.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), flip


# ── Full cell computation ───────────────────────────────────────────────────

def compute_cell(
    acriss_code: str,
    duration: int,
    provider_groups: dict[str, list[Decimal]],
    stale_providers: set[str],
    stale_days: dict[str, int],
    missing_providers: set[str],
    rule: RuleConfig,
    base: str,
    round_mode: str,
    rule_is_default: bool = True,
    total_providers: int = 0,
) -> CellResult:
    """Compute the recommended price for one (acriss_code, duration) cell.

    provider_groups: {provider_code: [total_price, ...]} — all groups for that provider.
    stale_providers: set of provider_codes with stale data.
    stale_days: {provider_code: days_since_last_good_scrape}
    missing_providers: provider_codes that have no offer for this cell.
    """
    n_total = total_providers or len(provider_groups) + len(missing_providers)

    # Step 1: intra-provider collapse
    collapsed = collapse_intra_provider(provider_groups)

    # Step 2: anomaly detection
    anomalies = detect_anomalies(collapsed)
    present: list[ProviderContribution] = []
    dropped: list[DroppedProvider] = []
    degraded = False

    for pk, total in collapsed.items():
        if anomalies.get(pk):
            dropped.append(DroppedProvider(
                provider_code=pk,
                total=total,
                reason="anomaly",
            ))
            degraded = True
        else:
            per_day = total / duration if duration else Decimal("0")
            present.append(ProviderContribution(
                provider_code=pk,
                total=total,
                per_day=per_day,
                stale=pk in stale_providers,
                stale_days=stale_days.get(pk, 0),
            ))

    for pk in missing_providers:
        dropped.append(DroppedProvider(provider_code=pk, total=None, reason="missing"))

    if not present:
        return CellResult(
            acriss_code=acriss_code,
            duration=duration,
            rec_total=None,
            rec_per_day=None,
            base_total=None,
            adjusted_total=None,
            clamped_total=None,
            floor_val=None,
            ceiling_val=None,
            present=present,
            dropped=dropped,
            flags=CellFlags(0, n_total, None, False, degraded, True),
            rule_used=rule,
            rule_is_default=rule_is_default,
        )

    valid_totals = [p.total for p in present]

    # Step 3: aggregation
    base_total = aggregate_cross_provider(valid_totals, base)
    assert base_total is not None

    # Mark which provider "won" (is closest to base_total)
    for p in present:
        if base == "min":
            p.is_base = (p.total == min(valid_totals))
        elif base == "max":
            p.is_base = (p.total == max(valid_totals))

    # Step 4: rule
    adjusted = apply_rule(base_total, rule)

    # Step 5: clamp
    floor_val, ceiling_val = compute_floor_ceiling(valid_totals, rule)
    clamped, clamped_which = apply_clamp(adjusted, floor_val, ceiling_val)

    # Step 6: rounding with directional guard
    rec_total, flip = apply_rounding_directional(clamped, base_total, rule, round_mode)
    rec_per_day = rec_total / duration if duration else Decimal("0")

    any_stale = any(p.stale for p in present)

    return CellResult(
        acriss_code=acriss_code,
        duration=duration,
        rec_total=rec_total,
        rec_per_day=rec_per_day,
        base_total=base_total,
        adjusted_total=adjusted,
        clamped_total=clamped,
        floor_val=floor_val,
        ceiling_val=ceiling_val,
        present=present,
        dropped=dropped,
        flags=CellFlags(
            coverage=len(present),
            total_providers=n_total,
            clamped=clamped_which,
            stale=any_stale,
            degraded=degraded,
            inferred=True,  # always True in V0: all prices from zone representative
        ),
        rule_used=rule,
        rule_is_default=rule_is_default,
        round_flip=flip,
    )
