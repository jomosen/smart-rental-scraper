from __future__ import annotations

from typing import List, Optional

from ...domain.models.season_internals import PricePoint
from ....shared.domain.models.result import BookingResult
from ....shared.domain.models.search import BookingSearch


class PricePointExtractor:
    """
    Translates (BookingSearch, BookingResult) pairs into PricePoint objects.

    SRP: sole responsibility is conversion between models.
    Does not analyse prices or make business decisions.

    For season detection not all groups or rates are needed:
    the first car with a valid rate is sufficient as a reference.
    If rate_name is specified, filters by that rate name.
    """

    def __init__(self, rate_name: Optional[str] = None) -> None:
        self._rate_name = rate_name

    def extract(
        self,
        searches: List[BookingSearch],
        results: List[BookingResult],
    ) -> List[PricePoint]:
        points = []
        for search, result in zip(searches, results):
            if not result or not result.cars:
                continue
            point = self._first_price_point(search, result)
            if point is not None:
                points.append(point)
        return points

    def _first_price_point(
        self,
        search: BookingSearch,
        result: BookingResult,
    ) -> Optional[PricePoint]:
        for car in result.cars:
            rate = self._find_rate(car)
            if rate is None:
                continue
            return PricePoint(
                pickup_date=search.pickup_at.date(),
                duration_days=search.rental_days,
                total_price=rate.total,
                car_group=car.group,
            )
        return None

    def _find_rate(self, car):
        if not car.rates:
            return None
        if self._rate_name:
            return next((r for r in car.rates if r.name == self._rate_name), None)
        return car.rates[0]
