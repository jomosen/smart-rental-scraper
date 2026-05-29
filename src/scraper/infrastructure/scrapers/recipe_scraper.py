"""RecipeScraper — BaseScraper adapter over the builder's recipe engine.

Loads the active Recipe from DB (cached after first load), then delegates
each _submit_form call to recipe_executor.run_recipe().  Zero LLM calls.

DEUDA ARQUITECTÓNICA (Option A — accepted for D2):
    RecipeScraper does NOT use the injected IBrowserDriver.  The injected
    driver is replaced with _NullDriver (no-op) so that BaseScraper's
    scrape_session() can call driver.launch() / driver.close() safely.
    The real Playwright session is managed internally via BrowserSession,
    kept alive across searches (D2.7) and closed in close().
"""
from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from decimal import Decimal
from typing import Callable, Optional

from ...domain.interfaces.driver import IBrowserDriver
from ...domain.models.booking_provider import BookingProvider
from ....shared.domain.models.search import BookingSearch
from ....shared.domain.models.result import BookingResult, Car, Rate
from .base_scraper import BaseScraper
from ..builder.browser_session import BrowserSession
from ..builder.recipe_executor import refine_dates, run_recipe
from ..builder.location_explorer import create_log_dir

logger = logging.getLogger(__name__)


class _NullDriver(IBrowserDriver):
    """No-op IBrowserDriver.

    RecipeScraper substitutes this for the injected driver because the real
    Playwright session is managed by BrowserSession internally.  BaseScraper's
    scrape_session() calls driver.launch() and driver.close(), both of which
    must be safe no-ops here.
    """

    async def launch(self, headless: bool = True) -> None:
        pass

    async def close(self) -> None:
        pass

    async def navigate(self, url: str) -> None:
        pass

    async def wait_for_load_state(self, state: str = "networkidle", timeout: int = 30000) -> None:
        pass

    async def click(self, selector: str, force: bool = False) -> None:
        pass

    async def select_option(self, selector: str, value: str) -> None:
        pass

    async def set_input_value(self, selector: str, value: str) -> None:
        pass

    async def js_click(self, selector: str) -> None:
        pass

    async def click_and_switch_tab(self, selector: str) -> None:
        pass

    async def scroll_to_top(self) -> None:
        pass

    async def get_page_source(self) -> str:
        return ""

    async def get_attribute(self, selector: str, attribute: str) -> str:
        return ""

    async def element_exists(self, selector: str) -> bool:
        return False

    async def wait_for_selector(self, selector: str, timeout: int = 5000, state: str = "visible") -> None:
        pass


def _map_transmission(raw: str | None) -> str | None:
    if raw is None:
        return None
    return "manual" if raw.upper().startswith("M") else "automatic"


def _map_vehicle(vr, criteria: BookingSearch) -> Car:
    """Map a VehicleResult from the builder engine to the domain Car model."""
    rental_days = max(criteria.rental_days, 1)
    total = Decimal(str(vr.price_final))
    return Car(
        model=vr.model or "",
        group=vr.group_code or "",
        description="",
        example_models=vr.model or "",
        seats=vr.seats,
        luggage=None,
        transmission=_map_transmission(vr.transmission),
        rates=[Rate(
            name="default",
            currency=vr.currency or "EUR",
            total=total,
            daily_price=total / rental_days,
        )],
    )


def _criteria_to_targets(criteria: BookingSearch) -> dict:
    """Derive a builder targets dict from a BookingSearch."""
    return {
        "location": criteria.pickup_location.display_name,
        "pickup_date": criteria.pickup_at.date(),
        "return_date": criteria.dropoff_at.date(),
        "pickup_time": criteria.pickup_at.strftime("%H:%M"),
        "return_time": criteria.dropoff_at.strftime("%H:%M"),
    }


class RecipeScraper(BaseScraper):
    """BaseScraper adapter that executes a pre-built Recipe from the DB.

    Inherits BaseScraper's session loop (probe/extract reuse pattern).

    Session lifecycle:
      - First _submit_form: opens a BrowserSession (headless=False), stored in
        self._session, and runs the full recipe (navigate → form → submit → extract).
      - Subsequent calls: _refine_form changes only the dates on the results page
        via refine_dates(), reusing the same BrowserSession.
      - On refine failure: BaseScraper falls back to _submit_form, which reuses the
        existing session but navigates back to recipe.url for a full reset.
      - close(): closes the BrowserSession and the AsyncExitStack that owns it.

    provider_id    — DB PK of the providers row; used to fetch the recipe.
    session_factory — callable returning a context manager that yields a
                      SQLAlchemy Session (e.g. partial(super_session, engine)).
                      Must be supplied for recipe loading to work.
    """

    def __init__(
        self,
        driver: IBrowserDriver,
        provider: Optional[BookingProvider] = None,
        provider_id: int = 0,
        session_factory: Optional[Callable] = None,
        pause_range: tuple[float, float] = (0.5, 1.5),
    ) -> None:
        # Replace injected driver with no-op — see module docstring.
        super().__init__(driver=_NullDriver(), provider=provider, pause_range=pause_range)
        self._provider_id = provider_id
        self._session_factory = session_factory
        self._recipe = None              # Recipe; loaded lazily on first _submit_form
        self._last_result = None         # ScrapeResult from the most recent run
        self._session: BrowserSession | None = None   # persistent Playwright session
        self._exit_stack = AsyncExitStack()            # owns self._session lifecycle

    async def _submit_form(self, criteria: BookingSearch) -> None:
        if self._recipe is None:
            self._recipe = self._load_recipe()

        if self._recipe is None:
            raise RuntimeError(
                f"No active recipe found for provider_id={self._provider_id}. "
                "Run experiments/scraper_builder/run_build_recipe.py first."
            )

        # Open browser session on first call; reuse on subsequent full submits.
        if self._session is None:
            self._session = await self._exit_stack.enter_async_context(
                BrowserSession(headless=False)
            )

        provider_name = self._provider.name if self._provider else "recipe_scraper"
        targets = _criteria_to_targets(criteria)
        log_dir = create_log_dir(provider_name, suffix="_scrape")

        result = await run_recipe(
            self._recipe, targets, log_dir,
            session=self._session,
        )

        if not result.success:
            raise RuntimeError(
                f"run_recipe failed at phase={result.failed_phase!r}: {result.error}"
            )

        if not result.vehicles:
            logger.warning("[%s] run_recipe returned 0 vehicles", provider_name)

        self._last_result = result

    async def _refine_form(self, criteria: BookingSearch) -> None:
        if self._session is None or self._recipe is None:
            # No live session yet — fall back to a full submit.
            await self._submit_form(criteria)
            return

        provider_name = self._provider.name if self._provider else "recipe_scraper"
        log_dir = create_log_dir(provider_name, suffix="_scrape")

        result = await refine_dates(
            session=self._session,
            recipe=self._recipe,
            pickup_date=criteria.pickup_at.date(),
            return_date=criteria.dropoff_at.date(),
            log_dir=log_dir,
        )

        if not result.success:
            raise RuntimeError(
                f"refine_dates failed at phase={result.failed_phase!r}: {result.error}"
            )

        self._last_result = result

    async def _extract_results(self, criteria: BookingSearch) -> BookingResult:
        if self._last_result is None:
            return BookingResult(
                provider_name=criteria.provider_name,
                errors=["_extract_results called before _submit_form"],
            )

        vehicles = getattr(self._last_result, "dom_vehicles", None) or self._last_result.vehicles
        cars = [
            _map_vehicle(vr, criteria)
            for vr in vehicles
            if vr.price_final is not None
        ]
        return BookingResult(provider_name=criteria.provider_name, cars=cars)

    def _load_recipe(self):
        """Fetch the active Recipe from DB synchronously. Returns None if absent."""
        if self._session_factory is None:
            logger.error("[RecipeScraper] session_factory not configured — cannot load recipe")
            return None

        from ..repositories.provider_recipe_repository import ProviderRecipeRepository

        with self._session_factory() as session:
            return ProviderRecipeRepository(session).get_active_recipe(self._provider_id)

    async def close(self) -> None:
        await self._exit_stack.aclose()
        self._session = None
        await super().close()
