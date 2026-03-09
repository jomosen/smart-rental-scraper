from __future__ import annotations

from datetime import date, timedelta
from typing import List

from ...domain.interfaces.smart_scraping import ISeasonAnalyzer
from ...domain.models.season import HomogeneousZone, PricePoint, SeasonBoundary


class SeasonAnalyzer(ISeasonAnalyzer):
    """
    Detects homogeneous zones by comparing the daily price of probe points.

    With uniform 7-day probes all points are directly comparable,
    so grouping by duration is not necessary.

    Algorithm:
      1. Filters PricePoints for the group.
      2. Detects SeasonBoundary (in daily price) between consecutive points.
      3. Builds HomogeneousZone between the detected boundaries.
      4. Assigns representative_date according to the configured strategy.
    """

    def __init__(
        self,
        price_change_threshold: float = 0.05,
        representative: str = "first",
    ) -> None:
        """
        price_change_threshold: minimum relative change in daily price
            to consider a season change has occurred (0.05 = 5%).
        representative: "first" uses the first day of the zone;
            "middle" uses the central day.
        """
        self._threshold = price_change_threshold
        self._representative = representative

    def detect_zones(
        self,
        price_points: List[PricePoint],
        period_start: date,
        period_end: date,
        car_group: str,
    ) -> List[HomogeneousZone]:
        relevant = sorted(
            [p for p in price_points if p.car_group == car_group],
            key=lambda p: p.pickup_date,
        )
        if not relevant:
            return [self._single_zone(period_start, period_end, 0.0, car_group)]

        boundaries = self._detect_boundaries(relevant)
        boundaries.sort(key=lambda b: b.left_date)
        return self._build_zones(boundaries, period_start, period_end, relevant, car_group)

    def _detect_boundaries(self, points: List[PricePoint]) -> List[SeasonBoundary]:
        """Detects boundaries by comparing daily price between consecutive points."""
        boundaries = []
        for prev, curr in zip(points, points[1:]):
            prev_daily = prev.total_price / max(prev.duration_days, 1)
            curr_daily = curr.total_price / max(curr.duration_days, 1)
            base = max(prev_daily, 0.01)
            change = abs(curr_daily - prev_daily) / base
            if change > self._threshold:
                boundaries.append(SeasonBoundary(
                    left_date=prev.pickup_date,
                    right_date=curr.pickup_date,
                    left_price=prev_daily,
                    right_price=curr_daily,
                ))
        return boundaries

    def _build_zones(
        self,
        boundaries: List[SeasonBoundary],
        period_start: date,
        period_end: date,
        points: List[PricePoint],
        car_group: str,
    ) -> List[HomogeneousZone]:
        # reference price = daily price
        daily_by_date = {
            p.pickup_date: p.total_price / max(p.duration_days, 1)
            for p in points
        }
        cuts = (
            [period_start]
            + [b.right_date for b in boundaries]
            + [period_end + timedelta(days=1)]
        )

        zones = []
        for zone_start, next_start in zip(cuts, cuts[1:]):
            zone_end = next_start - timedelta(days=1)
            ref_price = daily_by_date.get(zone_start) or next(
                (daily_by_date[d] for d in sorted(daily_by_date) if d >= zone_start),
                0.0,
            )
            zones.append(HomogeneousZone(
                start_date=zone_start,
                end_date=zone_end,
                reference_price=ref_price,
                car_group=car_group,
                representative_date=self._pick_representative(zone_start, zone_end),
            ))
        return zones

    def _single_zone(
        self, start: date, end: date, price: float, car_group: str
    ) -> HomogeneousZone:
        return HomogeneousZone(
            start_date=start,
            end_date=end,
            reference_price=price,
            car_group=car_group,
            representative_date=self._pick_representative(start, end),
        )

    def _pick_representative(self, start: date, end: date) -> date:
        if self._representative == "middle":
            return start + timedelta(days=(end - start).days // 2)
        return start
