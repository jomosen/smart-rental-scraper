from dataclasses import dataclass, field
from decimal import Decimal
from typing import List


@dataclass(frozen=True)
class Rate:
    name: str
    currency: str
    total: Decimal
    daily_price: Decimal


@dataclass(frozen=True)
class Car:
    model: str
    group: str        # E.g.: "Economy", "SUV", "Compact"
    description: str  # E.g.: "Manual, Air conditioning, 5 seats"
    rates: List[Rate] = field(default_factory=list)


@dataclass(frozen=True)
class BookingResult:
    # Synthetic results are produced by ResultExpander for in-memory
    # consumption (e.g. CSV export). The SaaS persistence layer must filter
    # these out — synthetic prices are derived on read from
    # homogeneous_zones, not stored. See docs/DATA_MODEL.md §6.
    provider_name: str
    cars: List[Car] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    is_synthetic: bool = False
