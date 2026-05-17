"""
Composition root: wires together all concrete implementations.

This is the only place in the codebase that is allowed to import
infrastructure classes (PlaywrightDriver, scrapers) and application
services together. main.py calls build_container() and receives a
ready-to-use AppContainer — it does not know how anything is constructed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Tuple

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
from ...infrastructure.scrapers.provider_a_scraper import ProviderAScraper
from ...infrastructure.scrapers.provider_b_scraper import ProviderBScraper
from ...infrastructure.scrapers.provider_c_scraper import ProviderCScraper
from ....saas.application.classification.acriss_loader import load_acriss_specs
from ....saas.infrastructure.classification.gemini_service import GeminiClassificationService

# Maps the "scraper" key in providers.json to its concrete class.
# Add one line here when creating a new scraper class.
SCRAPER_REGISTRY: dict[str, type[IBookingScraper]] = {
    "provider_a": ProviderAScraper,
    "provider_b": ProviderBScraper,
    "provider_c": ProviderCScraper,
}

_PROVIDERS_CONFIG = Path(__file__).parents[4] / "providers.json"

# (provider, pickup_location, dropoff_location, orchestrator)
OrchestratorEntry = Tuple[BookingProvider, Location, Location, SmartScraperOrchestrator]


@dataclass
class AppContainer:
    factory: ScraperFactory
    orchestrators: List[OrchestratorEntry]
    pickup_hour: int
    period_start: datetime
    period_end: datetime


def load_providers_raw(config_path: Path = _PROVIDERS_CONFIG) -> list[dict]:
    """Return the raw list of provider entries from providers.json."""
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


def build_container(
    pickup_hour: int,
    period_start: datetime,
    period_end: datetime,
    catalog_ids: dict[str, tuple[int, int, int]],
    session_factory: Callable,
) -> AppContainer:
    """
    Constructs and wires all application and infrastructure objects.

    catalog_ids: mapping of entry['name'] → (provider_id, location_id, rate_id),
                 returned by CatalogSyncService.sync_from_providers_json().
    session_factory: callable returning a context manager that yields a Session
                     (e.g. ``functools.partial(super_session, engine)``).
    """
    import os
    threshold = float(os.environ.get("SEASON_PRICE_THRESHOLD", "0.05"))

    yaml_path = Path(__file__).resolve().parents[4] / "acriss_codes.yaml"
    acriss_specs = load_acriss_specs(yaml_path)
    classification_service = GeminiClassificationService(
        acriss_types=acriss_specs,
    )

    entries = load_providers_raw()
    registry: dict[str, type[IBookingScraper]] = {}
    provider_configs: dict[str, BookingProvider] = {}

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
        registry[provider.name] = SCRAPER_REGISTRY[scraper_key]
        provider_configs[provider.name] = provider

    factory = ScraperFactory(
        registry=registry,
        driver_class=PlaywrightDriver,
        provider_configs=provider_configs,
    )

    orchestrators: List[OrchestratorEntry] = []
    for entry in entries:
        if not entry.get("enabled", True):
            continue

        provider = BookingProvider(name=entry["name"], base_url=entry["base_url"])
        location = Location(
            canonical_id=entry["location_id"],
            display_name=entry["location_name"],
        )
        rate_name = entry["rate_name"]
        provider_id, location_id, rate_id = catalog_ids[entry["name"]]

        orch = SmartScraperOrchestrator(
            factory=factory,
            probe=SeasonProbe(),
            analyzer=SeasonAnalyzer(price_change_threshold=threshold, representative="first"),
            plan_builder=SearchPlanBuilder(),
            extractor=PricePointExtractor(rate_name=rate_name),
            session_factory=session_factory,
            provider_id=provider_id,
            provider_location_id=location_id,
            provider_rate_id=rate_id,
            classification_service=classification_service,
            provider_code=entry["scraper"],
            location_code=entry["location_id"],
            rate_code=rate_name,
            rate_filter=RateFilter(rate_names=[rate_name]),
            pickup_hour=pickup_hour,
        )
        orchestrators.append((provider, location, location, orch))

    return AppContainer(
        factory=factory,
        orchestrators=orchestrators,
        pickup_hour=pickup_hour,
        period_start=period_start,
        period_end=period_end,
    )
