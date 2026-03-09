from dataclasses import dataclass, field
from typing import List
from .provider import BookingProvider

@dataclass(frozen=True)
class Rate:
    name: str
    currency: str
    total: float
    daily_price: float

@dataclass(frozen=True)
class Car:
    model: str
    group: str        # E.g.: "Economy", "SUV", "Compact"
    description: str  # E.g.: "Manual, Air conditioning, 5 seats"
    rates: List[Rate] = field(default_factory=list)

@dataclass(frozen=True)
class BookingResult:
    provider: BookingProvider
    cars: List[Car] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)