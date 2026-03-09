from dataclasses import dataclass
from datetime import datetime

from .provider import BookingProvider


@dataclass(frozen=True)
class Location:
    """
    Generic Value Object for a location.

    Uses a canonical identifier (e.g.: IATA, normalised name) that each
    scraper maps internally to its provider's nomenclature.
    """
    canonical_id: str   # E.g.: "MAD", "madrid-t4", "ES-MAD"
    display_name: str   # E.g.: "Madrid Airport T4"


@dataclass(frozen=True)
class BookingSearch:
    """Value Object representing the context of a search, provider-agnostic."""
    provider: BookingProvider
    pickup_location: Location
    dropoff_location: Location
    pickup_at: datetime
    dropoff_at: datetime

    @property
    def rental_days(self) -> int:
        return (self.dropoff_at - self.pickup_at).days
