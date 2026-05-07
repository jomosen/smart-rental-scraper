"""
Composition root: wires together all concrete implementations.

This is the only place in the codebase that is allowed to import
infrastructure classes (PlaywrightDriver, scrapers, repositories)
and application services together. main.py calls build_container()
and receives a ready-to-use AppContainer — it does not know how
anything is constructed.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

from ...domain.interfaces.scraper import IBookingScraper
from ...domain.models.booking_provider import BookingProvider
from ....shared.domain.models.search import Location
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
from ...infrastructure.scrapers.provider_c_scraper import ProviderCScraper

# Maps the "scraper" key in providers.json to its concrete class.
# Add one line here when creating a new scraper class.
SCRAPER_REGISTRY: dict[str, type[IBookingScraper]] = {
    "provider_a": ProviderAScraper,
    "provider_b": ProviderBScraper,
    "provider_c": ProviderCScraper,
}

_PROVIDERS_CONFIG = Path(__file__).parents[4] / "providers.json"

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


def _load_providers(
    config_path: Path,
) -> tuple[List[ProviderConfig], dict[str, type[IBookingScraper]]]:
    """
    Reads providers.json and returns the active provider list and their
    scraper registry, both built in a single pass.
    """
    with open(config_path, encoding="utf-8") as f:
        entries = json.load(f)

    providers: List[ProviderConfig] = []
    registry: dict[str, type[IBookingScraper]] = {}

    for entry in entries:
        if not entry.get("enabled", True):
            continue

        scraper_key = entry["scraper"]
        if scraper_key not in SCRAPER_REGISTRY:
            known = ", ".join(SCRAPER_REGISTRY)
            raise ValueError(
                f"Unknown scraper type '{scraper_key}' in providers.json. "
                f"Known types: {known}"
            )

        provider = BookingProvider(name=entry["name"], base_url=entry["base_url"])
        location = Location(
            canonical_id=entry["location_id"],
            display_name=entry["location_name"],
        )
        registry[provider.name] = SCRAPER_REGISTRY[scraper_key]
        providers.append((provider, location, location, entry["rate_name"]))

    return providers, registry


def build_container(
    pickup_hour: int,
    period_start: datetime,
    period_end: datetime,
) -> AppContainer:
    """
    Constructs and wires all application and infrastructure objects.
    Provider list is read from providers.json; global settings from env vars.
    """
    threshold = float(os.environ.get("SEASON_PRICE_THRESHOLD", "0.05"))

    providers, registry = _load_providers(_PROVIDERS_CONFIG)

    # Build provider_configs mapping (name → BookingProvider) for factory injection.
    # Each scraper receives its own BookingProvider at construction via the factory.
    provider_configs = {provider.name: provider for provider, _, _, _ in providers}
    factory = ScraperFactory(
        registry=registry,
        driver_class=PlaywrightDriver,
        provider_configs=provider_configs,
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
