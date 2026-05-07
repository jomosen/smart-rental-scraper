from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Dict, List, Set, Tuple

from ....shared.domain.models.result import BookingResult, Car
from ....shared.domain.models.search import BookingSearch, Location
from ....shared.domain.models.season import HomogeneousZone

# Pure domain pair: no application-layer wrappers (SearchRequest, RateFilter, etc.).
BookingPair = Tuple[BookingSearch, BookingResult]


class ResultExpander:
    """
    Fills dataset gaps by expanding the results of representative days
    to all days within their homogeneous zone.

    Within a homogeneous zone the price does not vary, so days without
    a direct result are filled with the result of their zone's representative day.
    Does not perform any additional searches.

    Operates exclusively on domain types (BookingSearch, BookingResult) —
    no dependency on the SearchRequest application wrapper.
    """

    def expand(
        self,
        pairs: List[BookingPair],
        provider_zones: List[Tuple[str, List[HomogeneousZone]]],
        period_start: date,
        period_end: date,
        durations: List[int],
        pickup_hour: int = 10,
    ) -> List[BookingPair]:
        if not pairs:
            return []

        # Index of real results: (provider_name, pickup_date, rental_days) → BookingResult
        result_index: Dict[Tuple[str, date, int], BookingResult] = {}
        # Template per provider: provider_name → (provider_name, pickup_loc, dropoff_loc)
        provider_templates: Dict[str, Tuple[str, Location, Location]] = {}

        for search, res in pairs:
            if not res:
                continue
            pname = res.provider_name
            if res.cars:
                key = (pname, search.pickup_at.date(), search.rental_days)
                result_index[key] = res
            if pname not in provider_templates:
                provider_templates[pname] = (
                    pname,
                    search.pickup_location,
                    search.dropoff_location,
                )

        expanded: List[BookingPair] = list(pairs)

        for provider_name, zones in provider_zones:
            if not zones or provider_name not in provider_templates:
                continue
            pname, pickup_loc, dropoff_loc = provider_templates[provider_name]

            current = period_start
            while current <= period_end:
                for duration in sorted(durations):
                    dropoff = current + timedelta(days=duration)
                    if dropoff > period_end:
                        continue
                    if (provider_name, current, duration) in result_index:
                        continue  # real data already exists

                    # For each group, find which zone covers `current`
                    # and group by representative_date → groups sharing that date
                    rep_to_groups: Dict[date, Set[str]] = {}
                    for zone in zones:
                        if zone.start_date <= current <= zone.end_date:
                            rep_to_groups.setdefault(zone.representative_date, set()).add(zone.car_group)

                    if not rep_to_groups:
                        continue

                    # Collect cars from the representative filtered by group
                    merged_cars: List[Car] = []
                    for rep_date, groups in rep_to_groups.items():
                        rep_result = result_index.get((provider_name, rep_date, duration))
                        if rep_result is None:
                            continue
                        for car in rep_result.cars:
                            if car.group in groups:
                                merged_cars.append(car)

                    if not merged_cars:
                        continue

                    pickup_dt = datetime(current.year, current.month, current.day, pickup_hour, 0, 0)
                    dropoff_dt = datetime(dropoff.year, dropoff.month, dropoff.day, pickup_hour, 0, 0)
                    synthetic_search = BookingSearch(
                        provider_name=pname,
                        pickup_location=pickup_loc,
                        dropoff_location=dropoff_loc,
                        pickup_at=pickup_dt,
                        dropoff_at=dropoff_dt,
                    )
                    synthetic_res = BookingResult(provider_name=pname, cars=merged_cars, is_synthetic=True)
                    expanded.append((synthetic_search, synthetic_res))

                current += timedelta(days=1)

        return expanded
