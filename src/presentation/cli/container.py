"""
Composition root: wires together all concrete implementations.

This is the only place in the codebase that is allowed to import
infrastructure classes (PlaywrightDriver, scrapers, repositories)
and application services together. main.py calls build_container()
and receives a ready-to-use AppContainer — it does not know how
anything is constructed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Tuple

from ...domain.models.provider import BookingProvider
from ...domain.models.search import Location
from ...application.factories.scraper_factory import ScraperFactory
from ...application.filters.rate_filter import RateFilter
from ...application.smart_scraping.price_point_extractor import PricePointExtractor
from ...application.smart_scraping.search_plan_builder import SearchPlanBuilder
from ...application.smart_scraping.season_analyzer import SeasonAnalyzer
from ...application.smart_scraping.season_probe import SeasonProbe
from ...application.smart_scraping.smart_orchestrator import SmartScraperOrchestrator
from ...infrastructure.playwright.playwright_driver import PlaywrightDriver
from ...infrastructure.repositories.json_season_boundary_repository import JsonSeasonBoundaryRepository
from ...infrastructure.scrapers.provider_a_scraper import ProviderAScraper
from ...infrastructure.scrapers.provider_b_scraper import ProviderBScraper

# (provider, pickup_location, dropoff_location, rate_name)
ProviderConfig = Tuple[BookingProvider, Location, Location, str]
# (provider, pickup_location, dropoff_location, orchestrator)
OrchestratorEntry = Tuple[BookingProvider, Location, Location, SmartScraperOrchestrator]


@dataclass
class AppContainer:
    factory: ScraperFactory
    orchestrators: List[OrchestratorEntry]
    providers: List[ProviderConfig]
    pickup_hour: int
    period_start: datetime
    period_end: datetime


def build_container(
    pickup_hour: int,
    period_start: datetime,
    period_end: datetime,
) -> AppContainer:
    """
    Constructs and wires all application and infrastructure objects.
    Reads configuration exclusively from environment variables.
    """
    provider_a = BookingProvider(
        name=os.environ["PROVIDER_A_NAME"],
        base_url=os.environ["PROVIDER_A_BASE_URL"],
    )
    provider_b = BookingProvider(
        name=os.environ["PROVIDER_B_NAME"],
        base_url=os.environ["PROVIDER_B_BASE_URL"],
    )
    loc_a = Location(
        canonical_id=os.environ["PROVIDER_A_LOCATION_ID"],
        display_name=os.environ["PROVIDER_A_LOCATION_NAME"],
    )
    loc_b = Location(
        canonical_id=os.environ["PROVIDER_B_LOCATION_ID"],
        display_name=os.environ["PROVIDER_B_LOCATION_NAME"],
    )

    threshold = float(os.environ.get("SEASON_PRICE_THRESHOLD", "0.05"))

    # To disable a provider, comment out its line.
    providers: List[ProviderConfig] = [
        (provider_a, loc_a, loc_a, os.environ["PROVIDER_A_RATE_NAME"]),
        (provider_b, loc_b, loc_b, os.environ["PROVIDER_B_RATE_NAME"]),
    ]

    factory = ScraperFactory(
        registry={
            provider_a.name: ProviderAScraper,
            provider_b.name: ProviderBScraper,
        },
        driver_class=PlaywrightDriver,
    )

    boundary_repo = JsonSeasonBoundaryRepository(storage_dir="seasons")

    orchestrators: List[OrchestratorEntry] = []
    for provider, pickup, dropoff, rate_name in providers:
        orch = SmartScraperOrchestrator(
            factory=factory,
            probe=SeasonProbe(),
            analyzer=SeasonAnalyzer(price_change_threshold=threshold, representative="first"),
            plan_builder=SearchPlanBuilder(),
            extractor=PricePointExtractor(rate_name=rate_name),
            boundary_repository=boundary_repo,
            rate_filter=RateFilter(rate_names=[rate_name]),
            pickup_hour=pickup_hour,
        )
        orchestrators.append((provider, pickup, dropoff, orch))

    return AppContainer(
        factory=factory,
        orchestrators=orchestrators,
        providers=providers,
        pickup_hour=pickup_hour,
        period_start=period_start,
        period_end=period_end,
    )
