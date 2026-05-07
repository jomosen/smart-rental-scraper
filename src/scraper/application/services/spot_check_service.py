from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional, Tuple

from ...domain.interfaces.scraper_factory import IScraperFactory
from ....shared.domain.models.result import BookingResult
from ..filters.rate_filter import RateFilter
from ..models.search_request import SearchRequest

logger = logging.getLogger(__name__)


@dataclass
class SpotCheckResult:
    provider: str
    pickup_date: str
    dropoff_date: str
    duration_days: int
    car_group: str
    rate_name: str
    original_total: Decimal
    live_total: Optional[Decimal]
    match: bool
    error: Optional[str] = None


class SpotCheckService:
    """
    Randomly selects N pairs from the extraction result,
    re-runs the searches live and compares exact prices.
    """

    def __init__(
        self,
        factory: IScraperFactory,
        sample_size: int = 5,
        rate_filter: Optional[RateFilter] = None,
    ) -> None:
        self._factory = factory
        self._sample_size = sample_size
        self._rate_filter = rate_filter

    async def run(
        self,
        pairs: List[Tuple[SearchRequest, BookingResult]],
    ) -> List[SpotCheckResult]:
        # Only pairs with valid results
        valid = [
            (req, res) for req, res in pairs
            if res and res.cars
        ]
        if not valid:
            logger.info("[SpotCheck] No valid pairs to check")
            return []

        sample = random.sample(valid, min(self._sample_size, len(valid)))
        logger.info("[SpotCheck] Checking %d random searches", len(sample))

        check_results = []
        for req, original_result in sample:
            live_result = await self._rescrape(req)
            check = self._compare(req, original_result, live_result)
            status = "OK" if check.match else ("ERROR" if check.error else "MISMATCH")
            logger.info(
                "[SpotCheck] %s | %dd | %s → %s | grupo=%s | original=%.2f€ | live=%s | %s",
                check.provider,
                check.duration_days,
                req.search.pickup_at.strftime("%d/%m"),
                req.search.dropoff_at.strftime("%d/%m"),
                check.car_group,
                check.original_total,
                f"{check.live_total:.2f}€" if check.live_total is not None else "N/A",
                status,
            )
            check_results.append(check)

        passed = sum(1 for c in check_results if c.match)
        logger.info(
            "[SpotCheck] Result: %d/%d correct",
            passed, len(check_results),
        )
        return check_results

    async def _rescrape(self, req: SearchRequest) -> Optional[BookingResult]:
        try:
            scraper = self._factory.create(req.search.provider_name)
            results = await scraper.scrape_session([req])
            return results[0] if results else None
        except Exception as exc:
            logger.error("[SpotCheck] Error re-scraping: %s", exc)
            return None

    def _compare(
        self,
        req: SearchRequest,
        original: BookingResult,
        live: Optional[BookingResult],
    ) -> SpotCheckResult:
        s = req.search
        base = SpotCheckResult(
            provider=s.provider_name,
            pickup_date=s.pickup_at.strftime("%d/%m/%Y"),
            dropoff_date=s.dropoff_at.strftime("%d/%m/%Y"),
            duration_days=s.rental_days,
            car_group="",
            rate_name="",
            original_total=Decimal(0),
            live_total=None,
            match=False,
        )

        # Extract the first car+rate reference from the original result
        orig_car, orig_rate = self._first_car_rate(original, req.rate_filter)
        if orig_car is None or orig_rate is None:
            base.error = "No reference rate in original result"
            return base

        base.car_group = orig_car.group
        base.rate_name = orig_rate.name
        base.original_total = orig_rate.total

        if live is None or not live.cars:
            base.error = "Live scrape returned no results"
            return base

        # Look for the same group and rate in the live result
        live_car = next((c for c in live.cars if c.group == orig_car.group), None)
        if live_car is None:
            base.error = f"Group '{orig_car.group}' not found in live result"
            return base

        live_rate = next((r for r in live_car.rates if r.name == orig_rate.name), None)
        if live_rate is None:
            base.error = f"Rate '{orig_rate.name}' not found in live result"
            return base

        base.live_total = live_rate.total
        base.match = live_rate.total == orig_rate.total
        return base

    @staticmethod
    def _first_car_rate(result: BookingResult, rate_filter: Optional[RateFilter]):
        for car in result.cars:
            if not car.rates:
                continue
            if rate_filter:
                rate = next(
                    (r for r in car.rates if r.name in rate_filter.rate_names), None
                )
            else:
                rate = car.rates[0]
            if rate:
                return car, rate
        return None, None
