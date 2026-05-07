from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import List

from ...domain.interfaces.smart_scraping import ISearchPlanBuilder
from ...domain.models.booking_provider import BookingProvider
from ....shared.domain.models.search import BookingSearch, Location
from ....shared.domain.models.season import HomogeneousZone


class SearchPlanBuilder(ISearchPlanBuilder):
    """
    Generates extraction searches from homogeneous zones.

    For each zone it produces one search per duration (short and long) using
    representative_date as pickup_date. Only includes combinations whose
    return date does not exceed period_end, avoiding out-of-period searches.

    Complexity: O(num_zones × len(durations))
    vs naive: O(num_days × len(durations))
    """

    def build_short_searches(
        self,
        zones: List[HomogeneousZone],
        provider: BookingProvider,
        pickup_location: Location,
        dropoff_location: Location,
        durations: List[int],
        period_end: date,
        pickup_hour: int = 10,
    ) -> List[BookingSearch]:
        seen: set[tuple[datetime, datetime]] = set()
        searches = []
        representative_dates = sorted({z.representative_date for z in zones})
        for rep_date in representative_dates:
            for days in sorted(durations):
                pickup_dt = datetime(
                    rep_date.year, rep_date.month, rep_date.day,
                    pickup_hour, 0, 0,
                )
                dropoff_dt = pickup_dt + timedelta(days=days)
                if dropoff_dt.date() > period_end:
                    continue
                key = (pickup_dt, dropoff_dt)
                if key in seen:
                    continue
                seen.add(key)
                searches.append(BookingSearch(
                    provider_name=provider.name,
                    pickup_location=pickup_location,
                    dropoff_location=dropoff_location,
                    pickup_at=pickup_dt,
                    dropoff_at=dropoff_dt,
                ))
        return searches
