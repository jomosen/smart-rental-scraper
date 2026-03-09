from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import List


@dataclass(frozen=True)
class PricePoint:
    """
    Price observed for a specific date and duration during the probing phase.
    duration_days takes values from the set of probe durations {7, 14, 21, 28}.
    car_group identifies the vehicle group used as the comparison reference.
    """
    pickup_date: date
    duration_days: int
    total_price: float
    car_group: str


@dataclass(frozen=True)
class SeasonBoundary:
    """
    Detected boundary between two adjacent seasons.
    left_date is the last day of the previous season.
    right_date is the first day of the new season.
    """
    left_date: date
    right_date: date
    left_price: float
    right_price: float


@dataclass(frozen=True)
class HomogeneousZone:
    """
    Date range within which the price is stable (same season).
    representative_date is the date used to extract all segments.
    """
    start_date: date
    end_date: date
    reference_price: float
    car_group: str
    representative_date: date

    @property
    def days(self) -> int:
        return (self.end_date - self.start_date).days + 1
