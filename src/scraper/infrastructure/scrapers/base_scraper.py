import asyncio
import logging
import os
import random
import time as _time
from abc import abstractmethod
from typing import Callable, List, Optional

from ...domain.interfaces.scraper import IBookingScraper
from ...domain.interfaces.driver import IBrowserDriver
from ...domain.models.booking_provider import BookingProvider
from ....shared.domain.models.search import BookingSearch
from ....shared.domain.models.result import BookingResult
from ...application.models.search_request import SearchRequest

logger = logging.getLogger(__name__)

# Anti-detection macro-pause tunables (overridable via .env).
# Set ANTIBOT_BREAK_DURATION_LOW=0 to disable entirely.
_BREAK_EVERY_MIN_LOW  = float(os.getenv("ANTIBOT_BREAK_EVERY_MIN_LOW",  "5"))
_BREAK_EVERY_MIN_HIGH = float(os.getenv("ANTIBOT_BREAK_EVERY_MIN_HIGH", "10"))
_BREAK_DURATION_LOW   = float(os.getenv("ANTIBOT_BREAK_DURATION_LOW",   "20"))
_BREAK_DURATION_HIGH  = float(os.getenv("ANTIBOT_BREAK_DURATION_HIGH",  "40"))


class BaseScraper(IBookingScraper):
    """
    Base class with shared behaviour across all scrapers.

    Implements the Template Method pattern: defines the general flow and
    delegates provider-specific steps to subclasses.
    """

    def __init__(
        self,
        driver: IBrowserDriver,
        provider: Optional[BookingProvider] = None,
        pause_range: tuple[float, float] = (0.5, 1.5),
    ) -> None:
        self._driver: IBrowserDriver = driver
        self._provider: Optional[BookingProvider] = provider
        self._pause_range = pause_range

    async def scrape_session(
        self,
        requests: List[SearchRequest],
        should_stop: Optional[Callable[[BookingResult], bool]] = None,
    ) -> List[BookingResult]:
        """
        Executes multiple searches reusing the same browser session.

        For each SearchRequest it attempts the primary search; if it fails, it tries
        the fallback_searches in order (intra-session, without closing the browser).
        The first successful search uses _submit_form; subsequent ones use _refine_form.
        After any failure, the next action uses _submit_form to recover the session.

        If should_stop is provided it is called after each result; returning True
        breaks the loop early — the triggering result is still included in results.
        """
        results = []
        needs_full_submit = True
        session_start = _time.monotonic()
        next_break_at = random.uniform(_BREAK_EVERY_MIN_LOW, _BREAK_EVERY_MIN_HIGH) * 60
        try:
            await self._driver.launch(headless=False)
            for ri, req in enumerate(requests):
                provider_name = req.search.provider_name
                s = req.search
                logger.info(
                    "[%s] Search %d/%d | %dd | %s → %s",
                    provider_name, ri + 1, len(requests),
                    s.rental_days,
                    s.pickup_at.strftime("%d/%m"),
                    s.dropoff_at.strftime("%d/%m"),
                )

                attempts = [req.search] + list(req.fallback_searches)
                result = None

                for attempt in attempts:
                    if attempt is not attempts[0]:
                        logger.info(
                            "[%s] Fallback search %d → %d days",
                            provider_name, ri + 1, attempt.rental_days,
                        )
                    try:
                        if needs_full_submit:
                            await self._submit_form(attempt)
                        else:
                            await self._refine_form(attempt)
                        result = await self._extract_results(attempt)
                        needs_full_submit = False
                        first_car = next((c for c in result.cars if c.rates), None)
                        if first_car:
                            logger.info(
                                "[%s]   → %s | %.2f€/day | %.2f€ total",
                                provider_name,
                                first_car.group,
                                first_car.rates[0].daily_price,
                                first_car.rates[0].total,
                            )
                        break
                    except Exception as exc:
                        logger.error(
                            "[%s] Search error %d (%dd): %s",
                            provider_name, ri + 1, attempt.rental_days, exc,
                        )
                        needs_full_submit = True

                if result is None:
                    logger.info(
                        "[%s] Retry search %d/%d from initial form",
                        provider_name, ri + 1, len(requests),
                    )
                    try:
                        await self._submit_form(req.search)
                        result = await self._extract_results(req.search)
                        needs_full_submit = False
                        logger.info(
                            "[%s] Retry search %d/%d succeeded",
                            provider_name, ri + 1, len(requests),
                        )
                    except Exception as exc:
                        logger.error(
                            "[%s] Retry search %d/%d failed: %s",
                            provider_name, ri + 1, len(requests), exc,
                        )
                        needs_full_submit = True
                        result = BookingResult(
                            provider_name=req.search.provider_name,
                            errors=["All attempts failed"],
                        )
                results.append(result)
                if should_stop is not None and should_stop(result):
                    logger.info(
                        "[%s] Probe stopped early at search %d/%d",
                        provider_name, ri + 1, len(requests),
                    )
                    break
                if _BREAK_DURATION_LOW > 0 or _BREAK_DURATION_HIGH > 0:
                    elapsed = _time.monotonic() - session_start
                    if elapsed >= next_break_at:
                        duration = random.uniform(_BREAK_DURATION_LOW, _BREAK_DURATION_HIGH)
                        logger.info(
                            "[%s] Anti-bot pause %.0fs after %.1fmin of session",
                            provider_name, duration, elapsed / 60,
                        )
                        await asyncio.sleep(duration)
                        next_break_at = (
                            (_time.monotonic() - session_start)
                            + random.uniform(_BREAK_EVERY_MIN_LOW, _BREAK_EVERY_MIN_HIGH) * 60
                        )
        finally:
            await self.close()
        return results

    async def _refine_form(self, criteria: BookingSearch) -> None:
        """
        Modifies dates from the results page to reuse the session.
        By default navigates to the initial form (fallback). Override in subclasses.
        """
        await self._submit_form(criteria)

    @abstractmethod
    async def _submit_form(self, criteria: BookingSearch) -> None:
        """Fills in and submits the provider's search form."""
        ...

    @abstractmethod
    async def _extract_results(self, criteria: BookingSearch) -> BookingResult:
        """Extracts and maps the page results to the domain model."""
        ...

    async def _pause(self, min_s: float | None = None, max_s: float | None = None) -> None:
        """Random pause to simulate human behaviour.
        Falls back to the instance's pause_range when min_s/max_s are not specified."""
        lo = min_s if min_s is not None else self._pause_range[0]
        hi = max_s if max_s is not None else self._pause_range[1]
        await asyncio.sleep(random.uniform(lo, hi))

    async def close(self) -> None:
        await self._driver.close()
