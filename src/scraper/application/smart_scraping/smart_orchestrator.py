from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field, replace
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
from ...domain.models.season_internals import PricePoint
from .price_point_extractor import PricePointExtractor

# SaaS persistence — accepted cross-boundary dependency for MVP ingestion layer.
from ....saas.application.classification.dtos import ClassificationResult, VehicleClassificationInput
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
from ....saas.infrastructure.persistence.repositories.provider_vehicle_category_repository import (
    attributes_hash,
)

logger = logging.getLogger(__name__)

_DEFAULT_EXTRACTION_DURATIONS = [1, 2, 3, 4, 5, 6, 7, 14, 21, 28]
_PROBE_STOP_THRESHOLD = 3


def _partition_for_classification(
    new_hashes: Dict[str, str],
    cached: Dict[str, Tuple[str, ClassificationResult]],
) -> Tuple[Dict[str, ClassificationResult], List[str]]:
    """Split groups into (reused results, names needing the LLM).

    A group is reused when it has a cached classification AND its attributes
    hash is unchanged. Everything else (new groups, changed attributes, or no
    usable cached code) is sent to the classifier. Pure — no I/O.
    """
    reused: Dict[str, ClassificationResult] = {}
    to_classify: List[str] = []
    for name, h in new_hashes.items():
        hit = cached.get(name)
        if hit is not None and hit[0] == h:
            reused[name] = hit[1]
        else:
            to_classify.append(name)
    return reused, to_classify


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

    Phase 2 — Analysis + PVC persistence + zone persistence:
        Detects homogeneous zones. Classifies all probe cars via
        classify_provider_batch (full catalog + prices). Persists PVCs.
        Writes zones to homogeneous_zones.

    Phase 3 — Selective extraction + observation persistence:
        Runs extraction durations once per zone representative date and writes
        price observations via PriceObservationRepository.insert_if_changed().

    Aggregation policy: multiple provider groups may share the same
    ACRISS code. Aggregation (MIN) happens at query time in
    PriceQueryService, not during persistence.
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
            probe_results = await self._run_session(
                probe_requests, should_stop=self._make_probe_stopper()
            )
            n_executed = next(
                (i for i, r in enumerate(probe_results) if r is None),
                len(probe_results),
            )
            probe_truncated = n_executed < len(probe_requests)
            if probe_truncated:
                logger.info(
                    "[%s] Probe stopped early after %d/%d searches (%d consecutive confirmed-empty)",
                    self._provider_code, n_executed, len(probe_requests), _PROBE_STOP_THRESHOLD,
                )

            # ── PHASE 2: Analysis ─────────────────────────────────────────
            logger.info("[%s] Phase 2 — Zone analysis", self._provider_code)
            price_points = self._extractor.extract(probe_searches, probe_results)

            # When the probe stopped early, limit zone detection to the range
            # actually covered so the last zone doesn't extend to the period end.
            if probe_truncated and price_points:
                effective_end = max(p.pickup_date for p in price_points)
            else:
                effective_end = end

            guide_group = self._select_guide_group(price_points)
            if guide_group is None:
                provider_zones = self._analyzer.detect_zones_provider_level(
                    price_points, start, effective_end,
                )
            else:
                provider_zones = self._analyzer.detect_zones(
                    price_points, start, effective_end, guide_group,
                )
                logger.info(
                    "[%s] Guide group for zone detection: %r (%d probe dates)",
                    self._provider_code, guide_group,
                    self._count_dates_for_group(price_points, guide_group),
                )
            provider_zones = self._reassign_representatives(provider_zones, price_points)
            logger.info(
                "[%s] Detected %d zone(s)",
                self._provider_code, len(provider_zones),
            )

            # Collect unique probe cars and compute mean price-per-day per group.
            # price_per_day derived as total/duration for consistency with persistence.
            probe_cars: Dict[str, Car] = {}
            group_daily_prices: Dict[str, List[float]] = {}
            group_currency: Dict[str, str] = {}
            for probe_search, result in zip(probe_searches, probe_results):
                if not result or not result.cars:
                    continue
                probe_dur = probe_search.rental_days
                for car in result.cars:
                    if car.group not in probe_cars:
                        probe_cars[car.group] = car
                    for rate in car.rates:
                        if rate.total and probe_dur:
                            group_daily_prices.setdefault(car.group, []).append(
                                float(rate.total / probe_dur)
                            )
                            if car.group not in group_currency and rate.currency:
                                group_currency[car.group] = rate.currency

            representative_prices: Dict[str, float] = {
                g: sum(ps) / len(ps)
                for g, ps in group_daily_prices.items()
            }

            code_to_classification = self._classify_probe_catalog(
                probe_cars, representative_prices, group_currency
            )
            self._persist_pvcs(probe_cars, code_to_classification)
            zones_count = self._persist_zones(provider_zones)

            # ── PHASE 3: Selective extraction ─────────────────────────────
            logger.info("[%s] Phase 3 — Extraction", self._provider_code)
            short_searches = self._plan_builder.build_short_searches(
                provider_zones, provider, pickup_location, dropoff_location,
                self._extraction_durations, end, self._pickup_hour,
            )
            short_requests = [SearchRequest(search=s, rate_filter=self._rate_filter)
                              for s in short_searches]
            short_results = await self._run_session(short_requests)

            inserted, skipped = self._persist_observations(
                short_requests, short_results, run_id, code_to_classification
            )

            elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
            stats: dict = {
                "zones": zones_count,
                "observations_inserted": inserted,
                "observations_skipped": skipped,
                "elapsed_seconds": round(elapsed, 1),
            }
            if probe_truncated:
                stats["probe_truncated"] = True
                stats["probe_truncated_after_search"] = n_executed
                stats["probe_truncated_reason"] = (
                    f"{_PROBE_STOP_THRESHOLD} consecutive confirmed-empty probes"
                )
            self._mark_run_finished(run_id, "success", stats)
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
    # Guide-group selection and representative-date reassignment
    # ------------------------------------------------------------------

    def _select_guide_group(self, price_points: List[PricePoint]) -> Optional[str]:
        """Pick the car_group present on the most distinct probe dates.

        The guide group is the most reliable season thermometer: the group
        the provider shows most consistently across the period.
        Ties are broken by total number of price points (more data = more reliable).
        Returns None when price_points is empty.
        """
        dates_by_group: dict[str, set] = defaultdict(set)
        count_by_group: dict[str, int] = defaultdict(int)
        for p in price_points:
            dates_by_group[p.car_group].add(p.pickup_date)
            count_by_group[p.car_group] += 1
        if not dates_by_group:
            return None
        return max(
            dates_by_group,
            key=lambda g: (len(dates_by_group[g]), count_by_group[g]),
        )

    def _count_dates_for_group(self, price_points: List[PricePoint], group: str) -> int:
        return len({p.pickup_date for p in price_points if p.car_group == group})

    def _reassign_representatives(
        self,
        zones: List[HomogeneousZone],
        price_points: List[PricePoint],
    ) -> List[HomogeneousZone]:
        """Set each zone's representative_date to the probe date within the zone
        that saw the most distinct car groups.

        Falls back to the existing representative_date when no probe date falls
        inside the zone (e.g. a zone produced from the fallback single-zone path).
        Ties in group count are broken by the later probe date (more inventory).
        """
        groups_by_date: dict[date, set] = defaultdict(set)
        for p in price_points:
            groups_by_date[p.pickup_date].add(p.car_group)

        updated: List[HomogeneousZone] = []
        for z in zones:
            candidates = [
                (d, len(groups))
                for d, groups in groups_by_date.items()
                if z.start_date <= d <= z.end_date
            ]
            if candidates:
                best_date = max(candidates, key=lambda c: (c[1], c[0]))[0]
                updated.append(replace(z, representative_date=best_date))
            else:
                updated.append(z)
        return updated

    # ------------------------------------------------------------------
    # Classification (probe phase)
    # ------------------------------------------------------------------

    def _classify_probe_catalog(
        self,
        probe_cars: Dict[str, Car],
        representative_prices: Dict[str, float],
        group_currency: Dict[str, str],
    ) -> Dict[str, ClassificationResult]:
        """Classify probe cars, calling the LLM only for new/changed groups.

        Groups whose attributes (example_models, seats, luggage) are unchanged
        since the last run reuse the stored ACRISS code — no LLM call, no cost.
        Returns a dict mapping group_name → ClassificationResult. Empty when no
        probe cars were seen.
        """
        if not probe_cars:
            return {}

        new_hashes = {
            name: attributes_hash(car.example_models or "", car.seats, car.luggage)
            for name, car in probe_cars.items()
        }
        cached = self._load_cached_classifications()
        reused, to_classify = _partition_for_classification(new_hashes, cached)

        logger.info(
            "[%s] Classification: %d reused (cached), %d via LLM",
            self._provider_code, len(reused), len(to_classify),
        )

        fresh: Dict[str, ClassificationResult] = {}
        if to_classify:
            vehicles = [
                VehicleClassificationInput(
                    external_code=name,
                    external_name=name,
                    example_models=probe_cars[name].example_models or "",
                    seats=probe_cars[name].seats,
                    luggage=probe_cars[name].luggage,
                    transmission=probe_cars[name].transmission,
                    fuel_type=None,
                    representative_price_7d=representative_prices.get(name),
                    representative_currency=group_currency.get(name),
                )
                for name in to_classify
            ]
            results = self._classification_service.classify_provider_batch(
                self._provider_code, vehicles
            )
            fresh = dict(zip(to_classify, results))

        return {**reused, **fresh}

    def _load_cached_classifications(
        self,
    ) -> Dict[str, Tuple[str, ClassificationResult]]:
        """Map external_code → (attributes_hash, stored ClassificationResult).

        Only includes active groups that already carry a real ACRISS code, so a
        previous failure (null code / API error) is retried rather than cached.
        """
        out: Dict[str, Tuple[str, ClassificationResult]] = {}
        with self._session_factory() as s:
            repo = ProviderVehicleCategoryRepository(s)
            existing = repo.list_active_for_tuple(
                self._provider_id, self._provider_location_id, self._provider_rate_id,
            )
            for pvc in existing:
                if pvc.external_code is None or pvc.acriss_category is None:
                    continue
                h = attributes_hash(pvc.example_models or "", pvc.seats, pvc.luggage)
                out[pvc.external_code] = (
                    h,
                    ClassificationResult(
                        acriss_category=pvc.acriss_category,
                        acriss_body_type=pvc.acriss_body_type,
                        acriss_transmission=pvc.acriss_transmission,
                        acriss_fuel=pvc.acriss_fuel,
                        confidence=pvc.classification_confidence or 0.0,
                        pending_review=pvc.pending_review,
                        rationale="reused: attributes unchanged since last run",
                    ),
                )
        return out

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

    def _persist_pvcs(
        self,
        probe_cars: Dict[str, Car],
        code_to_classification: Dict[str, ClassificationResult],
    ) -> None:
        """Upsert one PVC per car group seen in the probe phase."""
        if not probe_cars:
            return

        _pending_fallback = ClassificationResult(
            acriss_category=None,
            acriss_body_type=None,
            acriss_transmission=None,
            acriss_fuel=None,
            confidence=0.0,
            pending_review=True,
        )

        with self._session_factory() as s:
            pvc_repo = ProviderVehicleCategoryRepository(s)
            for group_name, car in probe_cars.items():
                classification = code_to_classification.get(group_name, _pending_fallback)
                pvc_repo.upsert_seen(
                    provider_id=self._provider_id,
                    provider_location_id=self._provider_location_id,
                    provider_rate_id=self._provider_rate_id,
                    external_code=group_name,
                    external_name=group_name,
                    example_models=car.example_models,
                    seats=car.seats,
                    luggage=car.luggage,
                    classification=classification,
                    transmission=car.transmission,
                )

    def _persist_zones(
        self,
        provider_zones: List[HomogeneousZone],
    ) -> int:
        """Replicate provider-level zones to every active PVC for this tuple.

        Returns total zone rows written (provider_zones × active PVCs).
        """
        with self._session_factory() as s:
            pvc_repo = ProviderVehicleCategoryRepository(s)
            zone_repo = HomogeneousZoneRepository(s)

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
        code_to_classification: Dict[str, ClassificationResult],
    ) -> Tuple[int, int]:
        """Persist price observations for all extraction results."""
        inserted = skipped = 0
        observed_at = datetime.now(timezone.utc)

        _pending_fallback = ClassificationResult(
            acriss_category=None,
            acriss_body_type=None,
            acriss_transmission=None,
            acriss_fuel=None,
            confidence=0.0,
            pending_review=True,
        )

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
                    classification = code_to_classification.get(car.group, _pending_fallback)
                    pvc = pvc_repo.upsert_seen(
                        provider_id=self._provider_id,
                        provider_location_id=self._provider_location_id,
                        provider_rate_id=self._provider_rate_id,
                        external_code=car.group,
                        external_name=car.group,
                        example_models=car.example_models,
                        seats=car.seats,
                        luggage=car.luggage,
                        classification=classification,
                        transmission=car.transmission,
                    )
                    for rate in car.rates:
                        did_insert = obs_repo.insert_if_changed(
                            provider_id=self._provider_id,
                            provider_location_id=self._provider_location_id,
                            provider_rate_id=self._provider_rate_id,
                            provider_vehicle_category_id=pvc.id,
                            scrape_run_id=run_id,
                            pickup_date=pickup_date,
                            duration_days=duration_days,
                            price_per_day=rate.total / duration_days,
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

    @staticmethod
    def _truncate_trailing_empties(
        searches: List[BookingSearch],
        results: List[Optional[BookingResult]],
        threshold: int = 3,
    ) -> tuple[List[BookingSearch], List[Optional[BookingResult]], Optional[date]]:
        """Remove a trailing run of confirmed-empty probes that exceeds *threshold*.

        Only ``is_confirmed_empty=True`` results count toward the streak.
        A ``None`` result (scraping error) or a result with ``is_confirmed_empty=False``
        RESETS the count — errors are recoverable and must not trigger a cut.

        Returns (effective_searches, effective_results, first_empty_date | None).
        ``first_empty_date`` is None when no truncation occurred.
        """
        streak = 0
        for r in reversed(results):
            if r is not None and r.is_confirmed_empty:
                streak += 1
            else:
                break

        if streak < threshold:
            return list(searches), list(results), None

        cutoff = len(results) - streak
        return (
            list(searches[:cutoff]),
            list(results[:cutoff]),
            searches[cutoff].pickup_at.date(),
        )

    def _make_probe_stopper(
        self, threshold: int = _PROBE_STOP_THRESHOLD
    ) -> Callable[[BookingResult], bool]:
        """Return a stateful should_stop callback for scrape_session.

        Counts consecutive is_confirmed_empty results; fires when the streak
        reaches threshold. Cars or errors (None / no is_confirmed_empty) reset
        the streak — errors are recoverable and must not trigger a cut.
        """
        state = {"streak": 0}

        def should_stop(result: BookingResult) -> bool:
            if result is not None and result.is_confirmed_empty:
                state["streak"] += 1
            else:
                state["streak"] = 0
            return state["streak"] >= threshold

        return should_stop

    async def _run_session(
        self,
        requests: List[SearchRequest],
        should_stop: Optional[Callable[[BookingResult], bool]] = None,
    ) -> List[Optional[BookingResult]]:
        return await run_session(self._factory, requests, should_stop=should_stop)
