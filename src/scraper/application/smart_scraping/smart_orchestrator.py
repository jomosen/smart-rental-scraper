from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Callable, List, Optional, Tuple

from ...domain.interfaces.scraper_factory import IScraperFactory
from ...domain.interfaces.smart_scraping import (
    ISearchPlanBuilder,
    ISeasonAnalyzer,
    ISeasonProbe,
)
from ...domain.models.booking_provider import BookingProvider
from ....shared.domain.models.result import BookingResult
from ....shared.domain.models.search import BookingSearch, Location
from ....shared.domain.models.season import HomogeneousZone
from ..filters.rate_filter import RateFilter
from ..models.search_request import SearchRequest
from ..services.session_runner import run_session
from .price_point_extractor import PricePointExtractor

# SaaS persistence — accepted cross-boundary dependency for MVP ingestion layer.
from ....saas.infrastructure.persistence.models.catalog import (
    HomogeneousZone as HzOrm,
)
from ....saas.infrastructure.persistence.repositories import (
    HomogeneousZoneRepository,
    PriceObservationRepository,
    ProviderVehicleGroupRepository,
    ScrapeRunRepository,
)

logger = logging.getLogger(__name__)

_DEFAULT_EXTRACTION_DURATIONS = [1, 2, 3, 4, 5, 6, 14, 21, 28]


@dataclass
class SmartScrapingResult:
    """Summary of a single provider scrape run."""
    provider_name: str
    run_id: int
    zones_detected: int
    observations_inserted: int
    observations_skipped: int


class SmartScraperOrchestrator:
    """
    Use case: intelligent scraping in three phases, with DB persistence.

    Phase 1 — Probing:
        Runs 7-day searches spaced weekly to detect season boundaries.

    Phase 2 — Analysis + zone persistence:
        Detects homogeneous zones and writes them to homogeneous_zones via
        HomogeneousZoneRepository.replace_zones_for_tuple().

    Phase 3 — Selective extraction + observation persistence:
        Runs extraction durations once per zone representative date and writes
        price observations via PriceObservationRepository.insert_if_changed().

    Scrape lifecycle:
        A ScrapeRun row is created at the start (status='running') and marked
        'success' or 'failed' at the end. Failures are logged and the run is
        marked without re-raising so the caller can continue with other providers.
    """

    def __init__(
        self,
        factory: IScraperFactory,
        probe: ISeasonProbe,
        analyzer: ISeasonAnalyzer,
        plan_builder: ISearchPlanBuilder,
        extractor: PricePointExtractor,
        session_factory: Callable,
        provider_id: int,
        provider_location_id: int,
        provider_rate_id: int,
        provider_code: str = "",
        location_code: str = "",
        rate_code: str = "",
        rate_filter: Optional[RateFilter] = None,
        pickup_hour: int = 10,
        extraction_durations: Optional[List[int]] = None,
    ) -> None:
        self._factory = factory
        self._probe = probe
        self._analyzer = analyzer
        self._plan_builder = plan_builder
        self._extractor = extractor
        self._session_factory = session_factory
        self._provider_id = provider_id
        self._provider_location_id = provider_location_id
        self._provider_rate_id = provider_rate_id
        self._provider_code = provider_code
        self._location_code = location_code
        self._rate_code = rate_code
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
        start_time = datetime.now(timezone.utc)
        start = period_start.date()
        end = period_end.date()

        run_id = self._create_scrape_run()
        logger.info(
            "[%s/%s/%s] run_id=%d — starting period %s → %s",
            self._provider_code, self._location_code, self._rate_code,
            run_id, start, end,
        )

        try:
            # ── PHASE 1: Probing ──────────────────────────────────────────
            logger.info("[%s] Phase 1 — Probing", self._provider_code)
            probe_searches = self._probe.build_probe_searches(
                provider, pickup_location, dropoff_location,
                start, end, self._pickup_hour,
            )
            probe_requests = [SearchRequest(search=s, rate_filter=self._rate_filter)
                              for s in probe_searches]
            probe_results = await self._run_session(probe_requests)

            # ── PHASE 2: Analysis ─────────────────────────────────────────
            logger.info("[%s] Phase 2 — Zone analysis", self._provider_code)
            price_points = self._extractor.extract(probe_searches, probe_results)
            car_groups = list({p.car_group for p in price_points}) or ["unknown"]

            all_zones: List[HomogeneousZone] = []
            for group in car_groups:
                zones = self._analyzer.detect_zones(price_points, start, end, group)
                all_zones.extend(zones)
                logger.info(
                    "[%s] Group '%s': %d zone(s)",
                    self._provider_code, group, len(zones),
                )

            zones_count = self._persist_zones(all_zones)

            # ── PHASE 3: Selective extraction ─────────────────────────────
            logger.info("[%s] Phase 3 — Extraction", self._provider_code)
            short_searches = self._plan_builder.build_short_searches(
                all_zones, provider, pickup_location, dropoff_location,
                self._extraction_durations, end, self._pickup_hour,
            )
            short_requests = [SearchRequest(search=s, rate_filter=self._rate_filter)
                              for s in short_searches]
            short_results = await self._run_session(short_requests)

            inserted, skipped = self._persist_observations(short_requests, short_results, run_id)

            elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
            self._mark_run_finished(run_id, "success", {
                "zones": zones_count,
                "observations_inserted": inserted,
                "observations_skipped": skipped,
                "elapsed_seconds": round(elapsed, 1),
            })
            logger.info(
                "[%s/%s/%s] run_id=%d — done in %.1fs | zones=%d inserted=%d skipped=%d",
                self._provider_code, self._location_code, self._rate_code,
                run_id, elapsed, zones_count, inserted, skipped,
            )

            return SmartScrapingResult(
                provider_name=provider.name,
                run_id=run_id,
                zones_detected=zones_count,
                observations_inserted=inserted,
                observations_skipped=skipped,
            )

        except Exception as exc:
            elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
            logger.error(
                "[%s/%s/%s] run_id=%d — FAILED after %.1fs: %s",
                self._provider_code, self._location_code, self._rate_code,
                run_id, elapsed, exc,
            )
            self._mark_run_finished(run_id, "failed", error=str(exc))
            raise

    # ------------------------------------------------------------------
    # DB operations (sync — called from async context; fast enough for MVP)
    # ------------------------------------------------------------------

    def _create_scrape_run(self) -> int:
        with self._session_factory() as s:
            repo = ScrapeRunRepository(s)
            run = repo.create(
                self._provider_id,
                self._provider_location_id,
                self._provider_rate_id,
            )
            run_id = run.id
        return run_id

    def _persist_zones(self, zones: List[HomogeneousZone]) -> int:
        """Persist zones grouped by car_group, replacing any previously active zones."""
        groups_to_zones: dict[str, List[HomogeneousZone]] = {}
        for zone in zones:
            groups_to_zones.setdefault(zone.car_group, []).append(zone)

        with self._session_factory() as s:
            vg_repo = ProviderVehicleGroupRepository(s)
            zone_repo = HomogeneousZoneRepository(s)

            for group_name, group_zones in groups_to_zones.items():
                vg = vg_repo.upsert_seen(
                    self._provider_id,
                    self._provider_location_id,
                    self._provider_rate_id,
                    group_name,
                    group_name,
                )
                orm_zones = [
                    HzOrm(
                        provider_id=self._provider_id,
                        provider_location_id=self._provider_location_id,
                        provider_rate_id=self._provider_rate_id,
                        provider_vehicle_group_id=vg.id,
                        start_date=z.start_date,
                        end_date=z.end_date,
                        representative_date=z.representative_date,
                        active=True,
                    )
                    for z in group_zones
                ]
                zone_repo.replace_zones_for_tuple(
                    self._provider_id,
                    self._provider_location_id,
                    self._provider_rate_id,
                    vg.id,
                    orm_zones,
                )

        return len(zones)

    def _persist_observations(
        self,
        requests: List[SearchRequest],
        results: List[BookingResult],
        run_id: int,
    ) -> Tuple[int, int]:
        """Persist price observations for all extraction results."""
        inserted = skipped = 0
        observed_at = datetime.now(timezone.utc)

        with self._session_factory() as s:
            vg_repo = ProviderVehicleGroupRepository(s)
            obs_repo = PriceObservationRepository(s)

            for req, result in zip(requests, results):
                if not result or not result.cars:
                    continue
                search = req.search
                pickup_date = search.pickup_at.date()
                duration_days = search.rental_days

                for car in result.cars:
                    for rate in car.rates:
                        vg = vg_repo.upsert_seen(
                            self._provider_id,
                            self._provider_location_id,
                            self._provider_rate_id,
                            car.group,
                            car.group,
                        )
                        did_insert = obs_repo.insert_if_changed(
                            provider_id=self._provider_id,
                            provider_location_id=self._provider_location_id,
                            provider_rate_id=self._provider_rate_id,
                            provider_vehicle_group_id=vg.id,
                            scrape_run_id=run_id,
                            pickup_date=pickup_date,
                            duration_days=duration_days,
                            price_per_day=rate.daily_price,
                            total_price=rate.total,
                            currency=rate.currency,
                            observed_at=observed_at,
                        )
                        if did_insert:
                            inserted += 1
                        else:
                            skipped += 1

        return inserted, skipped

    def _mark_run_finished(
        self,
        run_id: int,
        status: str,
        stats: Optional[dict] = None,
        error: Optional[str] = None,
    ) -> None:
        with self._session_factory() as s:
            ScrapeRunRepository(s).mark_finished(run_id, status, stats, error)

    async def _run_session(self, requests: List[SearchRequest]) -> List[BookingResult]:
        return await run_session(self._factory, requests)
