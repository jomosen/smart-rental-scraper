from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Location:
    """
    This is the scraper's view of a location, provider-agnostic. The SaaS
    catalog has a separate provider_locations entity with provider-specific
    codes (see docs/DATA_MODEL.md §2). The SaaS layer translates between
    the two; the scraper only sees Location.
    """
    canonical_id: str   # E.g.: "MAD", "madrid-t4", "ES-MAD"
    display_name: str   # E.g.: "Madrid Airport T4"


@dataclass(frozen=True)
class BookingSearch:
    """Value Object representing the context of a search, provider-agnostic."""
    provider_name: str
    pickup_location: Location
    dropoff_location: Location
    pickup_at: datetime
    dropoff_at: datetime

    @property
    def rental_days(self) -> int:
        """Returns the number of contracted 24-hour rental periods between
        pickup and dropoff. This matches the rent-a-car industry convention:
        a 'reservation from day 1 to day 8' means 7 days of rental —
        pickup on day 1, dropoff on day 8, seven 24h periods enjoyed.

        Assumes pickup_at and dropoff_at are the contractual times of the
        reservation, not the actual drop-off times (early returns are
        typically billed at the contracted duration)."""
        return (self.dropoff_at - self.pickup_at).days
