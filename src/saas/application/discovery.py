# Cross-boundary module: imports from src.scraper.* into src.saas.*
#
# This is acknowledged technical debt. ROADMAP_ARCHITECTURE.md
# specifies that the scraper will eventually run as a separate worker
# process communicating with the SaaS only via HTTP and the database.
# Until that separation is in place, onboarding needs to trigger a
# scrape inline, and the cleanest place to concentrate the coupling
# is here. No other file in src/saas/ should import from src/scraper/.
from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Callable

from src.scraper.application.factories.scraper_factory import ScraperFactory
from src.scraper.application.filters.rate_filter import RateFilter
from src.scraper.application.smart_scraping.price_point_extractor import PricePointExtractor
from src.scraper.application.smart_scraping.search_plan_builder import SearchPlanBuilder
from src.scraper.application.smart_scraping.season_analyzer import SeasonAnalyzer
from src.scraper.application.smart_scraping.season_probe import SeasonProbe
from src.scraper.application.smart_scraping.smart_orchestrator import SmartScraperOrchestrator
from src.scraper.domain.models.booking_provider import BookingProvider
from src.scraper.infrastructure.playwright.playwright_driver import PlaywrightDriver
from src.scraper.presentation.cli.container import SCRAPER_REGISTRY
from src.shared.domain.models.search import Location


def build_scraper_factory(providers_json: list[dict]) -> ScraperFactory:
    """Build a ScraperFactory for the providers listed in providers_json.

    Called by the onboarding CLI to construct the factory passed to step_discovery.
    Raises ValueError if any enabled entry references an unknown scraper key.
    """
    registry: dict[str, type] = {}
    provider_configs: dict[str, BookingProvider] = {}

    for entry in providers_json:
        if not entry.get("enabled", True):
            continue
        scraper_key = entry["scraper"]
        if scraper_key not in SCRAPER_REGISTRY:
            known = ", ".join(SCRAPER_REGISTRY)
            raise ValueError(
                f"Unknown scraper type '{scraper_key}' in providers.json. Known: {known}"
            )
        provider = BookingProvider(name=entry["name"], base_url=entry["base_url"])
        registry[provider.name] = SCRAPER_REGISTRY[scraper_key]
        provider_configs[provider.name] = provider

    return ScraperFactory(
        registry=registry,
        driver_class=PlaywrightDriver,
        provider_configs=provider_configs,
    )


def run_discovery_for_tuple(
    provider_code: str,
    location_code: str,
    rate_code: str,
    provider_id: int,
    provider_location_id: int,
    provider_rate_id: int,
    providers_json_entry: dict,
    session_factory: Callable,
    period_start: datetime,
    period_end: datetime,
    pickup_hour: int,
    scraper_factory: Callable,
) -> None:
    """Run the smart scraper for one (provider, location, rate) tuple.

    scraper_factory is required — pass the result of build_scraper_factory().
    Reads SEASON_PRICE_THRESHOLD from the environment (default 0.05).
    """
    threshold = float(os.environ.get("SEASON_PRICE_THRESHOLD", "0.05"))
    entry = providers_json_entry

    orch = SmartScraperOrchestrator(
        factory=scraper_factory,
        probe=SeasonProbe(),
        analyzer=SeasonAnalyzer(price_change_threshold=threshold, representative="first"),
        plan_builder=SearchPlanBuilder(),
        extractor=PricePointExtractor(rate_name=entry["rate_name"]),
        session_factory=session_factory,
        provider_id=provider_id,
        provider_location_id=provider_location_id,
        provider_rate_id=provider_rate_id,
        provider_code=entry["scraper"],
        location_code=entry["location_id"],
        rate_code=entry["rate_name"],
        rate_filter=RateFilter(rate_names=[entry["rate_name"]]),
        pickup_hour=pickup_hour,
    )

    provider_obj = BookingProvider(name=entry["name"], base_url=entry["base_url"])
    location_obj = Location(
        canonical_id=entry["location_id"],
        display_name=entry["location_name"],
    )
    asyncio.run(orch.run(provider_obj, location_obj, location_obj, period_start, period_end))
