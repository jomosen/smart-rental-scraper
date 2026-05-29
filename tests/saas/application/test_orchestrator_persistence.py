"""Integration tests for SmartScraperOrchestrator DB persistence.

Scrapers (Playwright) are mocked so no browser is launched. DB operations
run against the real local Postgres. Each test commits catalog setup so the
orchestrator's independent sessions can see it, then cleans up in finally.
"""
from __future__ import annotations

import datetime
import functools
from decimal import Decimal
from functools import partial
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import delete, select

from src.saas.application.catalog_sync import CatalogSyncService
from tests.saas.application._fakes import StubClassificationService
from src.scraper.application.factories.scraper_factory import ScraperFactory
from src.scraper.infrastructure.builder.extraction.models import VehicleResult
from src.scraper.infrastructure.builder.scraper_engine import ScrapeResult
from src.scraper.infrastructure.scrapers.recipe_scraper import RecipeScraper
from src.saas.infrastructure.persistence.engine import super_engine
from src.saas.infrastructure.persistence.models.catalog import (
    HomogeneousZone as HzOrm,
    PriceObservation,
    PriceObservationHeartbeat,
    Provider,
    ProviderLocation,
    ProviderRate,
    ProviderVehicleCategory,
    ScrapeRun,
)
from src.saas.infrastructure.persistence.session import super_session
from src.scraper.application.smart_scraping.price_point_extractor import PricePointExtractor
from src.scraper.application.smart_scraping.search_plan_builder import SearchPlanBuilder
from src.scraper.application.smart_scraping.season_analyzer import SeasonAnalyzer
from src.scraper.application.smart_scraping.season_probe import SeasonProbe
from src.scraper.application.smart_scraping.smart_orchestrator import SmartScraperOrchestrator
from src.scraper.domain.models.booking_provider import BookingProvider
from src.shared.domain.models.result import BookingResult, Car, Rate
from src.shared.domain.models.search import Location

_PERIOD_START = datetime.datetime(2026, 6, 1, 10, 0, 0)
_PERIOD_END = datetime.datetime(2026, 6, 10, 10, 0, 0)

_PROVIDER_ENTRY = {
    "name": "Orch Test Provider",
    "scraper": "orch_test_sc",
    "base_url": "https://orch-test.example.com",
    "location_id": "ORC",
    "location_name": "Orch Test Location",
    "rate_name": "Test Rate",
    "enabled": True,
}


def _setup_catalog(session) -> tuple[int, int, int]:
    """Upsert catalog rows and return (provider_id, location_id, rate_id)."""
    service = CatalogSyncService(session)
    ids = service.sync_from_providers_json([_PROVIDER_ENTRY])
    return ids["Orch Test Provider"]


def _cleanup_provider(session, provider_id: int) -> None:
    """Delete all data for provider_id in FK-safe order and commit.

    Called from test finally blocks — must succeed even if the test raised.
    """
    session.rollback()  # discard any open read transaction before deleting
    session.execute(delete(PriceObservation).where(PriceObservation.provider_id == provider_id))
    session.execute(delete(PriceObservationHeartbeat).where(PriceObservationHeartbeat.provider_id == provider_id))
    session.execute(delete(HzOrm).where(HzOrm.provider_id == provider_id))
    session.execute(delete(ScrapeRun).where(ScrapeRun.provider_id == provider_id))
    session.execute(delete(ProviderVehicleCategory).where(ProviderVehicleCategory.provider_id == provider_id))
    session.execute(delete(ProviderRate).where(ProviderRate.provider_id == provider_id))
    session.execute(delete(ProviderLocation).where(ProviderLocation.provider_id == provider_id))
    session.execute(delete(Provider).where(Provider.id == provider_id))
    session.commit()


def _make_orchestrator(
    provider_id: int,
    location_id: int,
    rate_id: int,
    session_factory,
    rate_name: str = "Test Rate",
    classification_service=None,
) -> SmartScraperOrchestrator:
    if classification_service is None:
        # Default stub: returns pending_review=True for any vehicle (no LLM calls)
        classification_service = StubClassificationService({})
    return SmartScraperOrchestrator(
        factory=None,  # not used — _run_session is mocked
        probe=SeasonProbe(),
        analyzer=SeasonAnalyzer(price_change_threshold=0.05, representative="first"),
        plan_builder=SearchPlanBuilder(),
        extractor=PricePointExtractor(rate_name=rate_name),
        session_factory=session_factory,
        provider_id=provider_id,
        provider_location_id=location_id,
        provider_rate_id=rate_id,
        classification_service=classification_service,
        provider_code="orch_test_sc",
        location_code="ORC",
        rate_code="Test Rate",
    )


def _empty_results(requests):
    """Return one empty BookingResult per request (no cars)."""
    return [BookingResult(provider_name="Orch Test Provider", cars=[]) for _ in requests]


def _results_with_groups(requests, groups: list[str], rate_name: str = "Test Rate"):
    """Return one BookingResult per request, each containing one Car per group."""
    cars = [
        Car(
            model=f"Car {g}", group=g, description="", example_models="",
            rates=[Rate(name=rate_name, currency="EUR",
                       total=Decimal("70.00"), daily_price=Decimal("10.00"))],
        )
        for g in groups
    ]
    return [BookingResult(provider_name="Orch Test Provider", cars=cars) for _ in requests]


class TestOrchestratorCreatesScrapeRun:
    async def test_creates_scrape_run_per_provider(self, super_db_session):
        provider_id, location_id, rate_id = _setup_catalog(super_db_session)
        super_db_session.commit()  # make visible to orchestrator's independent sessions
        session_factory = partial(super_session, super_engine())
        try:
            orch = _make_orchestrator(provider_id, location_id, rate_id, session_factory)
            provider = BookingProvider(name="Orch Test Provider", base_url="https://x.com")
            location = Location(canonical_id="ORC", display_name="Orch Test Location")

            with patch.object(SmartScraperOrchestrator, "_run_session",
                              new=AsyncMock(side_effect=_empty_results)):
                result = await orch.run(provider, location, location, _PERIOD_START, _PERIOD_END)

            assert result.run_id is not None
            run = super_db_session.get(ScrapeRun, result.run_id)
            assert run is not None
            assert run.status == "success"
            assert run.provider_id == provider_id
        finally:
            _cleanup_provider(super_db_session, provider_id)


class TestOrchestratorFailure:
    async def test_marks_run_failed_on_scraper_exception(self, super_db_session):
        provider_id, location_id, rate_id = _setup_catalog(super_db_session)
        super_db_session.commit()
        session_factory = partial(super_session, super_engine())
        try:
            orch = _make_orchestrator(provider_id, location_id, rate_id, session_factory)
            provider = BookingProvider(name="Orch Test Provider", base_url="https://x.com")
            location = Location(canonical_id="ORC", display_name="Orch Test Location")

            async def raise_on_first(requests):
                raise RuntimeError("simulated scraper failure")

            with pytest.raises(RuntimeError, match="simulated scraper failure"):
                with patch.object(SmartScraperOrchestrator, "_run_session",
                                  new=AsyncMock(side_effect=raise_on_first)):
                    await orch.run(provider, location, location, _PERIOD_START, _PERIOD_END)

            runs = super_db_session.scalars(
                select(ScrapeRun)
                .where(ScrapeRun.provider_id == provider_id)
                .order_by(ScrapeRun.started_at.desc())
            ).all()
            assert runs, "A ScrapeRun should have been created"
            assert runs[0].status == "failed"
            assert "simulated scraper failure" in (runs[0].error or "")
        finally:
            _cleanup_provider(super_db_session, provider_id)


class TestOrchestratorZonePersistence:
    async def test_persists_zones_via_replace(self, super_db_session):
        provider_id, location_id, rate_id = _setup_catalog(super_db_session)
        # Pre-seed one PVC so the empty-probe run has a group to replicate zones to
        super_db_session.add(ProviderVehicleCategory(
            provider_id=provider_id,
            provider_location_id=location_id,
            provider_rate_id=rate_id,
            external_code="ECMR",
            external_name="Economy",
            example_models="",
            active=True,
        ))
        super_db_session.commit()
        session_factory = partial(super_session, super_engine())
        try:
            orch = _make_orchestrator(provider_id, location_id, rate_id, session_factory)
            provider = BookingProvider(name="Orch Test Provider", base_url="https://x.com")
            location = Location(canonical_id="ORC", display_name="Orch Test Location")

            with patch.object(SmartScraperOrchestrator, "_run_session",
                              new=AsyncMock(side_effect=_empty_results)):
                result = await orch.run(provider, location, location, _PERIOD_START, _PERIOD_END)

            # zones_detected = total rows = provider_zones × active_groups
            assert result.zones_detected > 0
            zones = super_db_session.scalars(
                select(HzOrm)
                .where(HzOrm.provider_id == provider_id, HzOrm.active.is_(True))
            ).all()
            assert len(zones) > 0
        finally:
            _cleanup_provider(super_db_session, provider_id)


class TestOrchestratorZoneReplication:
    async def test_zones_replicated_to_all_active_provider_vehicle_groups(
        self, super_db_session
    ):
        """Zone rows equal provider_zones × active_groups after a run."""
        provider_id, location_id, rate_id = _setup_catalog(super_db_session)
        for code in ("ECMR", "CCAR", "FCAR"):
            super_db_session.add(ProviderVehicleCategory(
                provider_id=provider_id,
                provider_location_id=location_id,
                provider_rate_id=rate_id,
                external_code=code,
                external_name=code,
                example_models="",
                active=True,
            ))
        super_db_session.commit()
        session_factory = partial(super_session, super_engine())
        try:
            orch = _make_orchestrator(provider_id, location_id, rate_id, session_factory)
            provider = BookingProvider(name="Orch Test Provider", base_url="https://x.com")
            location = Location(canonical_id="ORC", display_name="Orch Test Location")

            # Probe empty → 1 provider-level zone; 3 pre-existing PVGs → 3 zone rows
            with patch.object(SmartScraperOrchestrator, "_run_session",
                              new=AsyncMock(side_effect=_empty_results)):
                result = await orch.run(provider, location, location, _PERIOD_START, _PERIOD_END)

            zones = super_db_session.scalars(
                select(HzOrm)
                .where(HzOrm.provider_id == provider_id, HzOrm.active.is_(True))
            ).all()
            # 1 provider-level zone replicated to each of the 3 active PVGs
            assert len(zones) == 3
            assert result.zones_detected == 3
        finally:
            _cleanup_provider(super_db_session, provider_id)

    async def test_zones_replicated_to_groups_discovered_in_probe(
        self, super_db_session
    ):
        """Pre-classified groups seen in probe get zones replicated to them."""
        provider_id, location_id, rate_id = _setup_catalog(super_db_session)
        # Pre-seed PVCs for groups the probe will return; ClassificationService
        # (prompt 3) is responsible for creating rows for unknown codes.
        for code in ("GrpA", "GrpB"):
            super_db_session.add(ProviderVehicleCategory(
                provider_id=provider_id,
                provider_location_id=location_id,
                provider_rate_id=rate_id,
                external_code=code,
                external_name=code,
                example_models="",
                active=True,
            ))
        super_db_session.commit()
        session_factory = partial(super_session, super_engine())
        try:
            orch = _make_orchestrator(provider_id, location_id, rate_id, session_factory)
            provider = BookingProvider(name="Orch Test Provider", base_url="https://x.com")
            location = Location(canonical_id="ORC", display_name="Orch Test Location")

            call_count = 0

            async def side_effect(requests):
                nonlocal call_count
                call_count += 1
                if call_count == 1:  # probe — 2 groups at stable price → 1 zone
                    return _results_with_groups(requests, ["GrpA", "GrpB"])
                return _empty_results(requests)  # extraction

            with patch.object(SmartScraperOrchestrator, "_run_session",
                              new=AsyncMock(side_effect=side_effect)):
                result = await orch.run(provider, location, location, _PERIOD_START, _PERIOD_END)

            zones = super_db_session.scalars(
                select(HzOrm)
                .where(HzOrm.provider_id == provider_id, HzOrm.active.is_(True))
            ).all()
            # 1 provider-level zone replicated to GrpA and GrpB = 2 zone rows
            assert len(zones) == 2
            pvg_ids = {z.provider_vehicle_category_id for z in zones}
            assert len(pvg_ids) == 2  # two distinct PVCs received zones
        finally:
            _cleanup_provider(super_db_session, provider_id)

    async def test_no_zones_persisted_when_no_groups_available(
        self, super_db_session
    ):
        """No PVGs, empty probe → warning logged, no zone rows, run succeeds."""
        provider_id, location_id, rate_id = _setup_catalog(super_db_session)
        super_db_session.commit()
        session_factory = partial(super_session, super_engine())
        try:
            orch = _make_orchestrator(provider_id, location_id, rate_id, session_factory)
            provider = BookingProvider(name="Orch Test Provider", base_url="https://x.com")
            location = Location(canonical_id="ORC", display_name="Orch Test Location")

            with patch.object(SmartScraperOrchestrator, "_run_session",
                              new=AsyncMock(side_effect=_empty_results)):
                result = await orch.run(provider, location, location, _PERIOD_START, _PERIOD_END)

            zones = super_db_session.scalars(
                select(HzOrm).where(HzOrm.provider_id == provider_id)
            ).all()
            assert len(zones) == 0
            assert result.zones_detected == 0

            run = super_db_session.get(ScrapeRun, result.run_id)
            assert run.status == "success"
        finally:
            _cleanup_provider(super_db_session, provider_id)


class TestOrchestratorObservationPersistence:
    async def test_persists_observations_via_insert_if_changed(self, super_db_session):
        provider_id, location_id, rate_id = _setup_catalog(super_db_session)
        # Pre-seed the PVC for "Economy" so upsert_seen can find and return it
        super_db_session.add(ProviderVehicleCategory(
            provider_id=provider_id,
            provider_location_id=location_id,
            provider_rate_id=rate_id,
            external_code="Economy",
            external_name="Economy",
            example_models="",
            active=True,
        ))
        super_db_session.commit()
        session_factory = partial(super_session, super_engine())
        try:
            orch = _make_orchestrator(provider_id, location_id, rate_id, session_factory)
            provider = BookingProvider(name="Orch Test Provider", base_url="https://x.com")
            location = Location(canonical_id="ORC", display_name="Orch Test Location")

            call_count = 0

            async def side_effect(requests):
                nonlocal call_count
                call_count += 1
                if call_count == 1:  # probe — empty, produces one "unknown" zone
                    return _empty_results(requests)
                # extraction — return one result with a car for each request
                return [
                    BookingResult(
                        provider_name="Orch Test Provider",
                        cars=[Car(
                            model="Test Car", group="Economy", description="", example_models="",
                            rates=[Rate(name="Test Rate", currency="EUR",
                                       total=Decimal("70.00"), daily_price=Decimal("10.00"))],
                        )],
                    )
                    for _ in requests
                ]

            with patch.object(SmartScraperOrchestrator, "_run_session",
                              new=AsyncMock(side_effect=side_effect)):
                result = await orch.run(provider, location, location, _PERIOD_START, _PERIOD_END)

            assert result.observations_inserted > 0
        finally:
            _cleanup_provider(super_db_session, provider_id)


# ── D3 milestone test ─────────────────────────────────────────────────────────

#: Three vehicles RecipeScraper's mock recipe engine returns for every search.
_D3_DOM_VEHICLES = [
    VehicleResult(model="Seat Ibiza",  group_code="Eco",     transmission="M", seats=5, price_final=30.0,  currency="EUR"),
    VehicleResult(model="Ford Focus",  group_code="Compact", transmission="M", seats=5, price_final=45.0,  currency="EUR"),
    VehicleResult(model="Audi A8",     group_code="Premium", transmission="A", seats=5, price_final=110.0, currency="EUR"),
]

#: ScrapeResult the mocked run_recipe returns — success, dom_vehicles populated, vehicles=[].
_D3_SCRAPE_RESULT = ScrapeResult(
    url="https://orch-test.example.com",
    targets={},
    success=True,
    failed_phase=None,
    error=None,
    vehicles=[],
    dom_vehicles=_D3_DOM_VEHICLES,
    form_fields=None,
    results_structure=None,
    verification=None,
    duration_seconds=0.1,
    cost_estimate_eur=0.0,
    llm_calls=0,
    scroll_rounds=0,
    scroll_final_count=0,
    has_empty_page=False,
)


class TestRecipeScraperPipelineFlow:
    """D3 milestone: full pipeline through RecipeScraper with builder engine mocked.

    Uses the real SmartScraperOrchestrator + real RecipeScraper code paths
    (scrape_session → _submit_form → _extract_results → dom_vehicles → Car).
    Only the external boundaries are mocked:
      - run_recipe  → returns _D3_SCRAPE_RESULT (no Playwright)
      - _open_session / _close_session → no-ops (no BrowserSession)
      - _load_recipe → returns a fake Recipe with refine_strategy="none"
      - create_log_dir → returns Path("/tmp") (no filesystem writes)
      - ClassificationService → StubClassificationService (no Gemini calls)

    Verifies that all four persistence tables are written correctly:
      scrape_runs · provider_vehicle_categories · homogeneous_zones · price_observations
    """

    async def test_full_pipeline_persists_four_tables(self, super_db_session):
        provider_id, location_id, rate_id = _setup_catalog(super_db_session)
        super_db_session.commit()
        session_factory = partial(super_session, super_engine())

        # Eco → EDMR (classified), Compact → CDMR (classified), Premium → pending_review
        classification_service = StubClassificationService({"Eco": "EDMR", "Compact": "CDMR"})

        # Recipe: refine_strategy="none" so _refine_form always delegates to _submit_form
        fake_recipe = MagicMock()
        fake_recipe.refine_strategy = "none"

        recipe_scraper_cls = functools.partial(
            RecipeScraper,
            provider_id=provider_id,
            session_factory=session_factory,
        )
        factory = ScraperFactory(
            registry={_PROVIDER_ENTRY["name"]: recipe_scraper_cls},
            driver_class=MagicMock,  # RecipeScraper replaces the injected driver internally
            provider_configs={
                _PROVIDER_ENTRY["name"]: BookingProvider(
                    name=_PROVIDER_ENTRY["name"],
                    base_url="https://orch-test.example.com",
                )
            },
        )

        orch = SmartScraperOrchestrator(
            factory=factory,
            probe=SeasonProbe(),
            analyzer=SeasonAnalyzer(price_change_threshold=0.05, representative="first"),
            plan_builder=SearchPlanBuilder(),
            extractor=PricePointExtractor(rate_name="Test Rate"),
            session_factory=session_factory,
            provider_id=provider_id,
            provider_location_id=location_id,
            provider_rate_id=rate_id,
            classification_service=classification_service,
            provider_code="orch_test_sc",
            location_code="ORC",
            rate_code="Test Rate",
        )

        provider = BookingProvider(name=_PROVIDER_ENTRY["name"], base_url="https://orch-test.example.com")
        location = Location(canonical_id="ORC", display_name="Orch Test Location")

        try:
            with (
                patch.object(RecipeScraper, "_load_recipe", return_value=fake_recipe),
                patch.object(RecipeScraper, "_open_session", new_callable=AsyncMock),
                patch.object(RecipeScraper, "_close_session", new_callable=AsyncMock),
                patch(
                    "src.scraper.infrastructure.scrapers.recipe_scraper.run_recipe",
                    new_callable=AsyncMock,
                    return_value=_D3_SCRAPE_RESULT,
                ),
                patch(
                    "src.scraper.infrastructure.scrapers.recipe_scraper.create_log_dir",
                    return_value=Path("/tmp"),
                ),
            ):
                result = await orch.run(provider, location, location, _PERIOD_START, _PERIOD_END)

            # ── scrape_runs ───────────────────────────────────────────────────
            run = super_db_session.get(ScrapeRun, result.run_id)
            assert run is not None
            assert run.status == "success"
            assert run.stats_jsonb is not None
            assert run.stats_jsonb["zones"] > 0
            assert run.stats_jsonb["observations_inserted"] > 0

            # ── provider_vehicle_categories ───────────────────────────────────
            pvcs = super_db_session.scalars(
                select(ProviderVehicleCategory)
                .where(ProviderVehicleCategory.provider_id == provider_id)
                .order_by(ProviderVehicleCategory.external_code)
            ).all()
            assert len(pvcs) == 3

            pvc_by_code = {p.external_code: p for p in pvcs}

            # Classified PVCs: ACRISS set, pending_review False
            eco = pvc_by_code["Eco"]
            assert eco.acriss_category == "E"
            assert eco.acriss_body_type == "D"
            assert eco.pending_review is False

            compact = pvc_by_code["Compact"]
            assert compact.acriss_category == "C"
            assert compact.acriss_body_type == "D"
            assert compact.pending_review is False

            # Unclassified PVC: ACRISS NULL, pending_review True
            premium = pvc_by_code["Premium"]
            assert premium.acriss_category is None
            assert premium.pending_review is True

            # ── homogeneous_zones ─────────────────────────────────────────────
            zones = super_db_session.scalars(
                select(HzOrm)
                .where(HzOrm.provider_id == provider_id, HzOrm.active.is_(True))
            ).all()
            # 1 provider-level zone replicated to each of the 3 PVCs
            assert len(zones) == 3
            pvc_ids = {z.provider_vehicle_category_id for z in zones}
            assert pvc_ids == {eco.id, compact.id, premium.id}

            # ── price_observations ────────────────────────────────────────────
            obs = super_db_session.scalars(
                select(PriceObservation)
                .where(PriceObservation.provider_id == provider_id)
            ).all()
            assert len(obs) > 0
            for o in obs:
                assert o.currency == "EUR"
                assert o.price_per_day > 0
                assert o.total_price > 0
                assert o.provider_vehicle_category_id in pvc_ids

        finally:
            _cleanup_provider(super_db_session, provider_id)
