from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import List

from ...domain.interfaces.smart_scraping import ISeasonProbe
from ...domain.models.booking_provider import BookingProvider
from ....shared.domain.models.search import BookingSearch, Location

PROBE_DURATION_DAYS = 7


class SeasonProbe(ISeasonProbe):
    """
    Generates probe searches with 7-day rentals spaced weekly.

    One sample per week is sufficient to detect season changes because:
    - Providers define seasons in weeks or months, never in days.
    - A 7-day duration cannot resolve boundaries with more precision than ±7 days
      regardless of how many points are generated.

    For a period of N days it generates floor(N/7) searches: one per week.
    """

    def build_probe_searches(
        self,
        provider: BookingProvider,
        pickup_location: Location,
        dropoff_location: Location,
        period_start: date,
        period_end: date,
        pickup_hour: int = 10,
    ) -> List[BookingSearch]:
        searches = []
        current = period_start
        while current + timedelta(days=PROBE_DURATION_DAYS) <= period_end:
            pickup_dt = datetime(current.year, current.month, current.day, pickup_hour, 0, 0)
            dropoff_dt = pickup_dt + timedelta(days=PROBE_DURATION_DAYS)
            searches.append(BookingSearch(
                provider_name=provider.name,
                pickup_location=pickup_location,
                dropoff_location=dropoff_location,
                pickup_at=pickup_dt,
                dropoff_at=dropoff_dt,
            ))
            current += timedelta(days=PROBE_DURATION_DAYS)
        return searches
