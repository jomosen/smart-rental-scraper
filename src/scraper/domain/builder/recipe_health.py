"""Deterministic health checks for a recipe execution result.

check_recipe_health() accepts any object duck-typed to ScrapeResult and
returns a RecipeHealthCheck without any LLM calls or I/O.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ── Thresholds ────────────────────────────────────────────────────────────────

PRICE_PRESENCE_MIN = 0.80
KEY_FIELD_PRESENCE_MIN = 0.80
SOFT_FIELD_PRESENCE_MIN = 0.50
PRICE_MIN = 5.0
PRICE_MAX = 50_000.0
OUT_OF_RANGE_FAIL_FRACTION = 0.20


@dataclass
class RecipeHealthCheck:
    healthy: bool
    failed_checks: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rationale: str = ""
    vehicle_count: int = 0
    pct_with_model: float = 0.0
    pct_with_price: float = 0.0
    pct_with_key_fields: float = 0.0
    prices_in_range: bool = True


def check_recipe_health(result) -> RecipeHealthCheck:
    """Evaluate the health of a recipe execution result (duck-typed ScrapeResult).

    Distinguishes recipe breakage from a legitimate empty result:
    - 0 vehicles + page showed no-availability message → healthy (warning)
    - 0 vehicles + content present but nothing extracted → broken
    """
    failed: list[str] = []
    warnings: list[str] = []

    if result.failed_phase is not None:
        reason = (
            f"phase: recipe failed at phase={result.failed_phase!r}"
            + (f" — {result.error}" if result.error else "")
        )
        failed.append(reason)
        return RecipeHealthCheck(
            healthy=False,
            failed_checks=failed,
            warnings=warnings,
            rationale=f"Recipe broken at phase {result.failed_phase!r}",
        )

    vehicle_count = len(result.vehicles)

    if vehicle_count == 0:
        if getattr(result, "has_empty_page", False):
            warnings.append(
                "0 vehicles — page showed no-availability message; legitimate empty result"
            )
            return RecipeHealthCheck(
                healthy=True,
                failed_checks=failed,
                warnings=warnings,
                rationale="No vehicles available for these dates (page confirmed)",
                vehicle_count=0,
            )
        else:
            failed.append(
                "vehicle_count: 0 vehicles extracted with no no-availability message"
                " — extractor likely broken"
            )
            return RecipeHealthCheck(
                healthy=False,
                failed_checks=failed,
                warnings=warnings,
                rationale="0 vehicles without empty-availability signal — recipe extraction broken",
                vehicle_count=0,
            )

    n = vehicle_count
    n_model = sum(1 for v in result.vehicles if v.model)
    n_group = sum(1 for v in result.vehicles if v.group_code)
    n_price = sum(1 for v in result.vehicles if v.price_final is not None)
    n_trans = sum(1 for v in result.vehicles if v.transmission)
    n_seats = sum(1 for v in result.vehicles if v.seats is not None)

    pct_with_model = n_model / n
    pct_group = n_group / n
    pct_with_price = n_price / n
    pct_trans = n_trans / n
    pct_seats = n_seats / n
    pct_with_key_fields = min(pct_with_model, pct_group, pct_with_price)

    if pct_with_price < PRICE_PRESENCE_MIN:
        failed.append(
            f"price_presence: {pct_with_price:.0%} have price"
            f" (min {PRICE_PRESENCE_MIN:.0%}) — price extractor likely broken"
        )
    if pct_with_model < KEY_FIELD_PRESENCE_MIN:
        failed.append(
            f"model_presence: {pct_with_model:.0%} have model"
            f" (min {KEY_FIELD_PRESENCE_MIN:.0%}) — model extractor likely broken"
        )
    if pct_group < KEY_FIELD_PRESENCE_MIN:
        failed.append(
            f"group_code_presence: {pct_group:.0%} have group_code"
            f" (min {KEY_FIELD_PRESENCE_MIN:.0%}) — group_code extractor likely broken"
        )

    if pct_trans < SOFT_FIELD_PRESENCE_MIN:
        warnings.append(f"transmission present in only {pct_trans:.0%} of vehicles")
    if pct_seats < SOFT_FIELD_PRESENCE_MIN:
        warnings.append(f"seats present in only {pct_seats:.0%} of vehicles")

    prices_in_range = True
    prices = [v.price_final for v in result.vehicles if v.price_final is not None]
    if prices:
        out_of_range = [p for p in prices if p < PRICE_MIN or p > PRICE_MAX]
        frac_oor = len(out_of_range) / len(prices)
        if frac_oor > OUT_OF_RANGE_FAIL_FRACTION:
            failed.append(
                f"price_range: {frac_oor:.0%} of prices outside"
                f" [{PRICE_MIN}, {PRICE_MAX}] — parse desync likely"
            )
            prices_in_range = False
        elif out_of_range:
            warnings.append(
                f"price_range: {len(out_of_range)} price(s) outside"
                f" [{PRICE_MIN}, {PRICE_MAX}]"
            )
            prices_in_range = False

    healthy = len(failed) == 0
    rationale = (
        f"{vehicle_count} vehicles, key fields present, prices in range"
        if healthy
        else "; ".join(failed)
    )

    return RecipeHealthCheck(
        healthy=healthy,
        failed_checks=failed,
        warnings=warnings,
        rationale=rationale,
        vehicle_count=vehicle_count,
        pct_with_model=pct_with_model,
        pct_with_price=pct_with_price,
        pct_with_key_fields=pct_with_key_fields,
        prices_in_range=prices_in_range,
    )
