"""Domain models for the extraction experiment."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class VehicleResult:
    model: str | None = None
    group_code: str | None = None
    transmission: str | None = None        # "M" | "A" | "Manual" | "Automático"
    seats: int | None = None
    price_final: float | None = None       # price the customer pays
    currency: str | None = None            # "EUR" / "USD" / "GBP"


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
