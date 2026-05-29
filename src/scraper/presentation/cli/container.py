"""
Composition root: wires together all concrete implementations.

This is the only place in the codebase that is allowed to import
infrastructure classes (PlaywrightDriver, scrapers) and application
services together. main.py calls build_container() and receives a
ready-to-use AppContainer — it does not know how anything is constructed.
"""
from __future__ import annotations

import functools
import os
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
from ...infrastructure.scrapers.victoria_scraper import VictoriaScraper
from ...infrastructure.scrapers.solcar_scraper import SolcarScraper
from ...infrastructure.scrapers.recipe_scraper import RecipeScraper
from ....saas.application.classification.acriss_loader import load_acriss_specs
from ....saas.infrastructure.classification.gemini_service import GeminiClassificationService
from ....saas.infrastructure.persistence.repositories import (
    ProviderLocationRepository,
    ProviderRateRepository,
    ProviderRepository,
)
from ....saas.infrastructure.persistence.models.catalog import (
    Provider as ProviderRow,
    ProviderLocation as ProviderLocationRow,
    ProviderRate as ProviderRateRow,
)

# Maps providers.scraper_key to the concrete scraper class.
# Add one line here when creating a new scraper class.
SCRAPER_REGISTRY: dict[str, type[IBookingScraper]] = {
    "provider_a": ProviderAScraper,
    "victoria": VictoriaScraper,
    "solcar": SolcarScraper,
    "centauro": RecipeScraper,
}

# (provider, pickup_location, dropoff_location, orchestrator)
OrchestratorEntry = Tuple[BookingProvider, Location, Location, SmartScraperOrchestrator]


@dataclass
class AppContainer:
    factory: ScraperFactory
    orchestrators: List[OrchestratorEntry]
    pickup_hour: int
    period_start: datetime
    period_end: datetime


# ── Catalog loading ────────────────────────────────────────────────────────────

CatalogEntry = Tuple[ProviderRow, ProviderLocationRow, ProviderRateRow]


def load_catalog_from_db(session) -> list[CatalogEntry]:
    """Return all active (provider, location, rate) triples from the DB.

    Requires an already-open session so that the returned ORM objects remain
    bound to it — callers must not close the session before consuming the rows.
    """
    provider_repo = ProviderRepository(session)
    location_repo = ProviderLocationRepository(session)
    rate_repo = ProviderRateRepository(session)

    entries: list[CatalogEntry] = []
    for provider in provider_repo.list_active():
        locations = location_repo.list_for_provider(provider.id)
        rates = rate_repo.list_for_provider(provider.id)
        for location in locations:
            for rate in rates:
                entries.append((provider, location, rate))
    return entries


# ── Container builder ──────────────────────────────────────────────────────────

def build_container(
    pickup_hour: int,
    period_start: datetime,
    period_end: datetime,
    session_factory: Callable,
) -> AppContainer:
    """
    Constructs and wires all application and infrastructure objects.

    Reads the provider catalog directly from the DB (providers table,
    status='active').  No providers.json required.

    session_factory: callable returning a context manager that yields a Session
                     (e.g. ``functools.partial(super_session, engine)``).
    """
    threshold = float(os.environ.get("SEASON_PRICE_THRESHOLD", "0.05"))

    yaml_path = Path(__file__).resolve().parents[4] / "acriss_codes.yaml"
    acriss_specs = load_acriss_specs(yaml_path)
    classification_service = GeminiClassificationService(acriss_types=acriss_specs)

    # Keep the session open for the entire build so ORM objects don't become
    # detached before we finish reading their attributes.
    with session_factory() as session:
        catalog = load_catalog_from_db(session)
        if not catalog:
            raise RuntimeError(
                "No active providers found in the database. "
                "Insert rows into the providers table (status='active') "
                "with the correct scraper_key, location, and rate."
            )

        registry: dict[str, object] = {}
        provider_configs: dict[str, BookingProvider] = {}

        for provider_row, _location_row, _rate_row in catalog:
            if provider_row.display_name in registry:
                continue  # already registered (multiple locations/rates share one factory entry)
            scraper_key = provider_row.scraper_key
            if scraper_key not in SCRAPER_REGISTRY:
                known = ", ".join(SCRAPER_REGISTRY)
                raise ValueError(
                    f"Provider '{provider_row.code}' has scraper_key='{scraper_key}' "
                    f"which is not in SCRAPER_REGISTRY. Known keys: {known}"
                )
            scraper_cls: object = SCRAPER_REGISTRY[scraper_key]
            if scraper_cls is RecipeScraper:
                scraper_cls = functools.partial(
                    RecipeScraper,
                    provider_id=provider_row.id,
                    session_factory=session_factory,
                )
            registry[provider_row.display_name] = scraper_cls
            provider_configs[provider_row.display_name] = BookingProvider(
                name=provider_row.display_name,
                base_url=provider_row.base_url or "",
            )

        factory = ScraperFactory(
            registry=registry,
            driver_class=PlaywrightDriver,
            provider_configs=provider_configs,
        )

        orchestrators: List[OrchestratorEntry] = []
        for provider_row, location_row, rate_row in catalog:
            provider = BookingProvider(
                name=provider_row.display_name,
                base_url=provider_row.base_url or "",
            )
            location = Location(
                canonical_id=location_row.location_code,
                display_name=location_row.location_name,
            )

            # Recipe-based scrapers emit Rate(name="default") for every car
            # because recipes don't yet distinguish among rate plans.  Applying
            # a name-based filter would discard all their vehicles.  Skip the
            # filter for now; re-enable it when recipes gain explicit rate-plan
            # awareness (deuda: add a flag/field in provider_rates or Recipe).
            scraper_cls = SCRAPER_REGISTRY.get(provider_row.scraper_key)
            is_recipe_based = scraper_cls is RecipeScraper
            rate_filter = None if is_recipe_based else RateFilter(
                rate_names=[rate_row.rate_name]
            )

            orch = SmartScraperOrchestrator(
                factory=factory,
                probe=SeasonProbe(),
                analyzer=SeasonAnalyzer(price_change_threshold=threshold, representative="first"),
                plan_builder=SearchPlanBuilder(),
                extractor=PricePointExtractor(
                    rate_name=None if is_recipe_based else rate_row.rate_name
                ),
                session_factory=session_factory,
                provider_id=provider_row.id,
                provider_location_id=location_row.id,
                provider_rate_id=rate_row.id,
                classification_service=classification_service,
                provider_code=provider_row.scraper_key,
                location_code=location_row.location_code,
                rate_code=rate_row.rate_code,
                rate_filter=rate_filter,
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
