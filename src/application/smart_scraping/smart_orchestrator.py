from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

from ...domain.interfaces.scraper_factory import IScraperFactory
from ...domain.interfaces.smart_scraping import (
    ISearchPlanBuilder,
    ISeasonAnalyzer,
    ISeasonBoundaryRepository,
    ISeasonProbe,
)
from ...domain.models.provider import BookingProvider
from ...domain.models.result import BookingResult
from ...domain.models.search import BookingSearch, Location
from ...domain.models.season import HomogeneousZone, SeasonBoundary
from ..filters.rate_filter import RateFilter
from ..models.search_request import SearchRequest
from ..services.session_runner import run_session
from .price_point_extractor import PricePointExtractor

logger = logging.getLogger(__name__)

_DEFAULT_EXTRACTION_DURATIONS = [1, 2, 3, 4, 5, 6, 14, 21, 28]


@dataclass
class SmartScrapingResult:
    """Complete result of a SmartScraperOrchestrator run."""
    provider_name: str
    pairs: List[Tuple[SearchRequest, BookingResult]] = field(default_factory=list)
    zones: List[HomogeneousZone] = field(default_factory=list)


class SmartScraperOrchestrator:
    """
    Use case: intelligent scraping in three phases.

    Phase 1 — Probing:
        Runs 7-day searches spaced weekly to detect season
        boundaries within the period.

    Phase 2 — Analysis:
        Converts results into PricePoints, detects homogeneous zones
        and saves boundaries in parallel without blocking extraction.

    Phase 3 — Selective extraction:
        Runs all extraction durations once per zone,
        using each zone's representative date.

    Depends exclusively on interfaces (DIP). Compatible with the
    existing infrastructure: reuses IScraperFactory without modifying it.
    """

    def __init__(
        self,
        factory: IScraperFactory,
        probe: ISeasonProbe,
        analyzer: ISeasonAnalyzer,
        plan_builder: ISearchPlanBuilder,
        extractor: PricePointExtractor,
        boundary_repository: Optional[ISeasonBoundaryRepository] = None,
        rate_filter: Optional[RateFilter] = None,
        pickup_hour: int = 10,
        extraction_durations: Optional[List[int]] = None,
    ) -> None:
        self._factory = factory
        self._probe = probe
        self._analyzer = analyzer
        self._plan_builder = plan_builder
        self._extractor = extractor
        self._boundary_repo = boundary_repository
        self._rate_filter = rate_filter
        self._pickup_hour = pickup_hour
        self._extraction_durations = extraction_durations or _DEFAULT_EXTRACTION_DURATIONS

    async def run(
        self,
        provider: BookingProvider,
        pickup_location: Location,
        dropoff_location: Location,
        period_start: datetime,
        period_end: datetime,
    ) -> SmartScrapingResult:
        start = period_start.date()
        end = period_end.date()

        # ── PHASE 1: Probing ──────────────────────────────────────────
        logger.info("[%s] Phase 1 — Probing period %s → %s", provider.name, start, end)
        probe_searches = self._probe.build_probe_searches(
            provider, pickup_location, dropoff_location,
            start, end, self._pickup_hour,
        )
        logger.info("[%s] %d probe searches generated", provider.name, len(probe_searches))
        probe_requests = [self._build_probe_request(s) for s in probe_searches]
        probe_results = await self._run_session(probe_requests)

        # ── PHASE 2: Analysis ─────────────────────────────────────────
        logger.info("[%s] Phase 2 — Homogeneous zone analysis", provider.name)
        price_points = self._extractor.extract(probe_searches, probe_results)

        car_groups = list({p.car_group for p in price_points}) or ["unknown"]
        all_zones: List[HomogeneousZone] = []
        all_boundaries: List[SeasonBoundary] = []

        for group in car_groups:
            zones = self._analyzer.detect_zones(price_points, start, end, group)
            all_zones.extend(zones)
            all_boundaries.extend(self._extract_boundaries(zones))
            logger.info(
                "[%s] Group '%s': %d zone(s) detected", provider.name, group, len(zones)
            )
            for z in zones:
                logger.info(
                    "  · %s → %s  ref price: %.2f€  representative: %s",
                    z.start_date, z.end_date, z.reference_price, z.representative_date,
                )

        # Save boundaries in parallel (fire-and-forget, does not block extraction)
        if self._boundary_repo and all_zones:
            asyncio.create_task(
                self._save_boundaries(
                    provider.name, car_groups, all_boundaries, all_zones, start, end
                )
            )

        # ── PHASE 3: Selective extraction ─────────────────────────────
        logger.info("[%s] Phase 3 — Short-segment extraction per zone", provider.name)
        short_searches = self._plan_builder.build_short_searches(
            all_zones, provider, pickup_location, dropoff_location,
            self._extraction_durations, end, self._pickup_hour,
        )
        logger.info("[%s] %d short-segment searches generated", provider.name, len(short_searches))
        short_requests = [SearchRequest(search=s, rate_filter=self._rate_filter) for s in short_searches]
        short_results = await self._run_session(short_requests)

        all_pairs = (
            list(zip(probe_requests, probe_results))
            + list(zip(short_requests, short_results))
        )

        total = len(probe_requests) + len(short_requests)
        naive = (end - start).days * (1 + len(self._extraction_durations))
        savings = round((1 - total / max(naive, 1)) * 100, 1)
        logger.info(
            "[%s] Total searches: %d (naive: ~%d, savings: %s%%)",
            provider.name, total, naive, savings,
        )

        return SmartScrapingResult(
            provider_name=provider.name,
            pairs=all_pairs,
            zones=all_zones,
        )

    # ------------------------------------------------------------------
    # Helpers privados
    # ------------------------------------------------------------------

    def _build_probe_request(self, search: BookingSearch) -> SearchRequest:
        """Builds a 7-day probe SearchRequest.

        7-day probes work across all providers without needing a fallback:
        it is the most common and universally supported duration.
        """
        return SearchRequest(search=search, rate_filter=self._rate_filter)

    async def _run_session(
        self, requests: List[SearchRequest]
    ) -> List[BookingResult]:
        """Groups by provider and runs in parallel across providers,
        sequentially within each one."""
        return await run_session(self._factory, requests)

    @staticmethod
    def _extract_boundaries(zones: List[HomogeneousZone]) -> List[SeasonBoundary]:
        """Reconstructs SeasonBoundary objects from adjacent zones."""
        boundaries = []
        for prev, curr in zip(zones, zones[1:]):
            boundaries.append(SeasonBoundary(
                left_date=prev.end_date,
                right_date=curr.start_date,
                left_price=prev.reference_price,
                right_price=curr.reference_price,
            ))
        return boundaries

    async def _save_boundaries(
        self,
        provider: str,
        car_groups: List[str],
        boundaries: List[SeasonBoundary],
        zones: List[HomogeneousZone],
        period_start: date,
        period_end: date,
    ) -> None:
        """Persists boundaries asynchronously. Errors are logged
        but do not interrupt the main flow."""
        try:
            for group in car_groups:
                group_zones = [z for z in zones if z.car_group == group]
                group_boundaries = [
                    b for b in boundaries
                    if any(z.car_group == group for z in zones
                           if z.end_date == b.left_date or z.start_date == b.right_date)
                ]
                await self._boundary_repo.save(
                    provider, group, group_boundaries, group_zones,
                    period_start, period_end,
                )
        except Exception as exc:
            logger.error("[%s] Error saving season boundaries: %s", provider, exc)
