from dataclasses import dataclass
from datetime import date
from decimal import Decimal

# PricePoint carries Decimal prices (same precision as Rate.total) so that
# SeasonAnalyzer can read them directly without conversion.
# SeasonBoundary is a pure analysis artifact feeding float threshold comparisons;
# it intentionally stays float. Neither type is persisted — see DATA_MODEL.md §6.


@dataclass(frozen=True)
class PricePoint:
    """
    Price observed for a specific date and duration during the probing phase.
    duration_days takes values from the set of probe durations {7, 14, 21, 28}.
    car_group identifies the vehicle group used as the comparison reference.
    """
    pickup_date: date
    duration_days: int
    total_price: Decimal
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
