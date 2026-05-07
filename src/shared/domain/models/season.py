from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class HomogeneousZone:
    """
    Date range within which the price is stable (same season).
    representative_date is the date used to extract all segments.
    Persisted to the SaaS database as homogeneous_zones (see docs/DATA_MODEL.md §6).
    """
    start_date: date
    end_date: date
    reference_price: Decimal
    car_group: str
    representative_date: date

    @property
    def days(self) -> int:
        return (self.end_date - self.start_date).days + 1
