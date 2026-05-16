from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

from ...domain.interfaces.scraper_factory import IScraperFactory
from ...domain.interfaces.smart_scraping import (
    ISearchPlanBuilder,
    ISeasonAnalyzer,
    ISeasonProbe,
)
from ...domain.models.booking_provider import BookingProvider
from ....shared.domain.models.result import BookingResult, Car
from ....shared.domain.models.search import BookingSearch, Location
from ....shared.domain.models.season import HomogeneousZone
from ..filters.rate_filter import RateFilter
from ..models.search_request import SearchRequest
from ..services.session_runner import run_session
from .price_point_extractor import PricePointExtractor

# SaaS persistence — accepted cross-boundary dependency for MVP ingestion layer.
from ....saas.application.classification.service import ClassificationService
from ....saas.infrastructure.persistence.models.catalog import (
    HomogeneousZone as HzOrm,
)
from ....saas.infrastructure.persistence.repositories import (
    HomogeneousZoneRepository,
    PriceObservationRepository,
    ProviderVehicleCategoryRepository,
    ScrapeRunRepository,
)

logger = logging.getLogger(__name__)

_DEFAULT_EXTRACTION_DURATIONS = [1, 2, 3, 4, 5, 6, 7, 14, 21, 28]


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
        classification_service: ClassificationService,
        taxonomy_version: int,
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
        self._classification_service = classification_service
        self._taxonomy_version = taxonomy_version
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
            provider_zones = self._analyzer.detect_zones_provider_level(
                price_points, start, end,
            )
            logger.info(
                "[%s] Detected %d provider-level zone(s)",
                self._provider_code, len(provider_zones),
            )
            probe_cars: Dict[str, Car] = {}
            for result in probe_results:
                if result and result.cars:
                    for car in result.cars:
                        if car.group not in probe_cars:
                            probe_cars[car.group] = car
            zones_count = self._persist_zones(provider_zones, probe_cars)

            # ── PHASE 3: Selective extraction ─────────────────────────────
            logger.info("[%s] Phase 3 — Extraction", self._provider_code)
            short_searches = self._plan_builder.build_short_searches(
                provider_zones, provider, pickup_location, dropoff_location,
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

    def _persist_zones(
        self,
        provider_zones: List[HomogeneousZone],
        probe_cars: Dict[str, Car],
    ) -> int:
        """Persist provider-level zones, replicated to every active
        provider_vehicle_group of the tuple.

        Returns the total count of zone rows written (provider_zones × groups).

        Limitation: only replicates zones to groups visible in this probe
        OR already in catalog from previous runs. Groups that exist for
        the provider but appear only in phase-3 extraction (rare) won't
        get zones until the next run. Acceptable: subsequent runs close
        the gap.
        """
        with self._session_factory() as s:
            pvc_repo = ProviderVehicleCategoryRepository(s)
            zone_repo = HomogeneousZoneRepository(s)

            # Ensure all groups seen in probe are in the catalog
            for group_name, car in probe_cars.items():
                pvc_repo.upsert_seen(
                    provider_id=self._provider_id,
                    provider_location_id=self._provider_location_id,
                    provider_rate_id=self._provider_rate_id,
                    external_code=group_name,
                    external_name=group_name,
                    example_models=car.example_models,
                    seats=car.seats,
                    luggage=car.luggage,
                    transmission=car.transmission,
                    fuel_type=None,
                    classification_service=self._classification_service,
                    taxonomy_version=self._taxonomy_version,
                )

            active_groups = pvc_repo.list_active_for_tuple(
                self._provider_id,
                self._provider_location_id,
                self._provider_rate_id,
            )

            if not active_groups:
                logger.warning(
                    "[%s] No active provider_vehicle_groups for tuple — "
                    "zones not persisted",
                    self._provider_code,
                )
                return 0

            total_rows = 0
            for vg in active_groups:
                orm_zones = [
                    HzOrm(
                        provider_id=self._provider_id,
                        provider_location_id=self._provider_location_id,
                        provider_rate_id=self._provider_rate_id,
                        provider_vehicle_category_id=vg.id,
                        start_date=z.start_date,
                        end_date=z.end_date,
                        representative_date=z.representative_date,
                        active=True,
                    )
                    for z in provider_zones
                ]
                zone_repo.replace_zones_for_tuple(
                    self._provider_id,
                    self._provider_location_id,
                    self._provider_rate_id,
                    vg.id,
                    orm_zones,
                )
                total_rows += len(orm_zones)

            logger.info(
                "[%s] Persisted %d zone(s) × %d group(s) = %d rows",
                self._provider_code, len(provider_zones), len(active_groups), total_rows,
            )
            return total_rows

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
            pvc_repo = ProviderVehicleCategoryRepository(s)
            obs_repo = PriceObservationRepository(s)

            for req, result in zip(requests, results):
                if not result or not result.cars:
                    continue
                search = req.search
                pickup_date = search.pickup_at.date()
                duration_days = search.rental_days

                for car in result.cars:
                    for rate in car.rates:
                        pvc = pvc_repo.upsert_seen(
                            provider_id=self._provider_id,
                            provider_location_id=self._provider_location_id,
                            provider_rate_id=self._provider_rate_id,
                            external_code=car.group,
                            external_name=car.group,
                            example_models=car.example_models,
                            seats=car.seats,
                            luggage=car.luggage,
                            transmission=car.transmission,
                            fuel_type=None,
                            classification_service=self._classification_service,
                            taxonomy_version=self._taxonomy_version,
                        )
                        did_insert = obs_repo.insert_if_changed(
                            provider_id=self._provider_id,
                            provider_location_id=self._provider_location_id,
                            provider_rate_id=self._provider_rate_id,
                            provider_vehicle_category_id=pvc.id,
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
