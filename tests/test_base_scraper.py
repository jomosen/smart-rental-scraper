"""Unit tests for BaseScraper.scrape_session should_stop callback.

No browser launched — driver methods are mocked with AsyncMock.
asyncio.run() is used instead of pytest-asyncio to keep the test setup simple.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.scraper.infrastructure.scrapers.base_scraper import BaseScraper
from src.scraper.application.models.search_request import SearchRequest
from src.shared.domain.models.result import BookingResult


# ---------------------------------------------------------------------------
# Minimal concrete scraper for testing
# ---------------------------------------------------------------------------

class _FakeScraper(BaseScraper):
    """Concrete BaseScraper that returns pre-configured results without a browser."""

    def __init__(self, results: list[BookingResult]) -> None:
        driver = MagicMock()
        driver.launch = AsyncMock()
        driver.close = AsyncMock()
        super().__init__(driver)
        self._results = results
        self._call_count = 0

    async def _submit_form(self, criteria) -> None:
        pass

    async def _extract_results(self, criteria) -> BookingResult:
        result = self._results[self._call_count]
        self._call_count += 1
        return result


def _make_req() -> SearchRequest:
    search = MagicMock()
    search.provider_name = "test"
    search.rental_days = 7
    search.pickup_at.strftime.return_value = "01/06"
    search.dropoff_at.strftime.return_value = "08/06"
    return SearchRequest(search=search)


def _ok() -> BookingResult:
    return BookingResult(provider_name="test")


def _empty() -> BookingResult:
    return BookingResult(provider_name="test", is_confirmed_empty=True)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestScrapeSessionShouldStop:
    def test_no_stopper_runs_all_requests(self):
        n = 5
        scraper = _FakeScraper([_ok()] * n)
        reqs = [_make_req() for _ in range(n)]
        out = asyncio.run(scraper.scrape_session(reqs, should_stop=None))
        assert len(out) == n

    def test_stopper_stops_after_threshold(self):
        """should_stop returning True on the 3rd call → only 3 results returned."""
        count = [0]

        def stopper(r: BookingResult) -> bool:
            count[0] += 1
            return count[0] >= 3

        scraper = _FakeScraper([_ok()] * 10)
        reqs = [_make_req() for _ in range(10)]
        out = asyncio.run(scraper.scrape_session(reqs, should_stop=stopper))
        assert len(out) == 3

    def test_triggering_result_is_included(self):
        """The result that caused should_stop=True is still in the output."""
        captured: list[BookingResult] = []

        def stopper(r: BookingResult) -> bool:
            captured.append(r)
            return len(captured) >= 2

        r1, r2, r3 = _ok(), _empty(), _ok()
        scraper = _FakeScraper([r1, r2, r3])
        reqs = [_make_req() for _ in range(3)]
        out = asyncio.run(scraper.scrape_session(reqs, should_stop=stopper))
        assert len(out) == 2
        assert out[1].is_confirmed_empty  # r2 triggered the stop

    def test_stopper_never_fires_returns_all(self):
        results = [_ok(), _empty(), _ok()]
        scraper = _FakeScraper(results)
        reqs = [_make_req() for _ in range(3)]
        out = asyncio.run(scraper.scrape_session(reqs, should_stop=lambda r: False))
        assert len(out) == 3

    def test_stop_at_first_result(self):
        scraper = _FakeScraper([_empty()] * 5)
        reqs = [_make_req() for _ in range(5)]
        out = asyncio.run(scraper.scrape_session(reqs, should_stop=lambda r: True))
        assert len(out) == 1
