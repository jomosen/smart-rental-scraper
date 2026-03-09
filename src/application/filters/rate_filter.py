from dataclasses import dataclass, field
from typing import List, Optional

from ...domain.models.result import BookingResult, Car


@dataclass(frozen=True)
class RateFilter:
    """Filters the rates of a BookingResult.

    rate_names: if provided, keeps only rates whose names match.
    exclude_zero_price: if True (default), discards rates with total == 0.
    """

    rate_names: Optional[List[str]] = None
    exclude_zero_price: bool = True

    def apply(self, result: BookingResult) -> BookingResult:
        filtered_cars = [
            Car(
                model=car.model,
                group=car.group,
                description=car.description,
                rates=[r for r in car.rates if self._keep(r)],
            )
            for car in result.cars
        ]
        return BookingResult(
            provider=result.provider,
            cars=[car for car in filtered_cars if car.rates],
            errors=result.errors,
        )

    def _keep(self, rate) -> bool:
        if self.exclude_zero_price and rate.total == 0:
            return False
        if self.rate_names and rate.name not in self.rate_names:
            return False
        return True
