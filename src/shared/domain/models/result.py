from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional


@dataclass(frozen=True)
class Rate:
    name: str
    currency: str
    total: Decimal
    daily_price: Decimal


@dataclass(frozen=True)
class Car:
    model: str
    group: str           # E.g.: "Economy", "SUV", "Compact"
    description: str     # E.g.: "Manual, Air conditioning, 5 seats"
    example_models: str  # E.g.: "Fiat Panda, Kia Picanto" — provider-shown representative models
    seats: Optional[int] = None
    luggage: Optional[int] = None
    transmission: Optional[str] = None  # 'manual' | 'automatic' | None
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
    # True when the provider explicitly reported "no prices available"
    # (e.g. a modal or empty-page message), as opposed to a scraping error.
    # Only confirmed-empty results count for the probe truncation threshold;
    # scraping errors (exceptions / timeouts) are treated as recoverable.
    is_confirmed_empty: bool = False
