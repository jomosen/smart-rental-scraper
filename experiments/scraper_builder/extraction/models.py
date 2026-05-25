"""Domain models for the extraction experiment."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class VehicleResult:
    model: str | None = None
    group_code: str | None = None
    availability_note: str | None = None   # "o similar" | "garantizado" | None
    category: str | None = None            # provider tier, e.g. "Económico"
    transmission: str | None = None        # "M" | "A" | "Manual" | "Automático"
    seats: int | None = None
    doors: int | None = None
    bags: int | None = None
    rate_type: str | None = None           # e.g. "Premium", "Básica"
    price_final: float | None = None       # price the customer pays (post-discount)
    price_original: float | None = None    # struck-through price, if present
    currency: str | None = None            # "EUR" / "$"
    discount_pct: float | None = None


@dataclass
class FieldSelector:
    field: str           # "model", "group_code", "price_final", ...
    selector: str        # CSS selector relative to the vehicle card
    extraction: str      # "text" | "attribute:ATTR" | "regex:PATTERN"
    rationale: str


@dataclass
class ResultsStructure:
    vehicle_card_selector: str             # CSS selector matching each vehicle card
    field_selectors: list[FieldSelector]
    price_strategy: str                    # how to obtain the definitive final price
    rationale: str
