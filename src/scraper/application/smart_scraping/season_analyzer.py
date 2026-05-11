from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List

from ...domain.interfaces.smart_scraping import ISeasonAnalyzer
from ...domain.models.season_internals import PricePoint, SeasonBoundary
from ....shared.domain.models.season import HomogeneousZone


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
            return [self._single_zone(period_start, period_end, Decimal(0), car_group)]

        boundaries = self._detect_boundaries(relevant)
        boundaries.sort(key=lambda b: b.left_date)
        return self._build_zones(boundaries, period_start, period_end, relevant, car_group)

    def detect_zones_provider_level(
        self,
        price_points: List[PricePoint],
        period_start: date,
        period_end: date,
    ) -> List[HomogeneousZone]:
        """Detect zones aggregating ALL price points, ignoring car_group.

        Use this when zones reflect the provider's calendar (the typical
        rent-a-car case) rather than per-group seasonality. Resulting
        zones carry car_group="" — the caller is responsible for
        replicating them to each provider_vehicle_group when persisting.
        """
        if not price_points:
            return [self._single_zone(period_start, period_end, Decimal(0), "")]

        aggregated = self._aggregate_by_date(price_points)
        boundaries = self._detect_boundaries(aggregated)
        boundaries.sort(key=lambda b: b.left_date)
        return self._build_zones(boundaries, period_start, period_end, aggregated, "")

    def _aggregate_by_date(self, price_points: List[PricePoint]) -> List[PricePoint]:
        """Return one synthetic PricePoint per unique pickup_date with mean daily price.

        Groups multiple car_group entries on the same date into a single
        representative point so boundary detection operates on the temporal
        signal, not across-group price differences.
        """
        by_date: Dict[date, List[Decimal]] = {}
        for p in price_points:
            daily = p.total_price / max(p.duration_days, 1)
            by_date.setdefault(p.pickup_date, []).append(daily)

        result: List[PricePoint] = []
        for d in sorted(by_date):
            prices = by_date[d]
            avg = sum(prices) / len(prices)
            result.append(PricePoint(
                pickup_date=d,
                duration_days=1,
                total_price=avg,
                car_group="",
            ))
        return result

    def _detect_boundaries(self, points: List[PricePoint]) -> List[SeasonBoundary]:
        """Detects boundaries by comparing daily price between consecutive points."""
        boundaries = []
        for prev, curr in zip(points, points[1:]):
            # Convert to float here: SeasonBoundary.left/right_price are float
            # (threshold comparisons don't need Decimal precision).
            prev_daily = float(prev.total_price) / max(prev.duration_days, 1)
            curr_daily = float(curr.total_price) / max(curr.duration_days, 1)
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
                Decimal(0),
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
        self, start: date, end: date, price: Decimal, car_group: str
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
