from abc import ABC, abstractmethod
from typing import Callable, List, Optional

from .driver import IBrowserDriver
from ....shared.domain.models.result import BookingResult


class IBookingScraper(ABC):
    """
    Core contract for all booking scrapers.
    The only entry point is scrape_session(), which reuses the browser
    across multiple searches to minimise overhead.
    """

    def __init__(self, driver: IBrowserDriver) -> None:
        self.driver = driver

    @abstractmethod
    async def scrape_session(
        self,
        requests: list,
        should_stop: Optional[Callable[[BookingResult], bool]] = None,
    ) -> List[BookingResult]:
        """Executes multiple searches reusing a single browser session.

        If should_stop is provided it is called after each result; returning
        True breaks the loop — the triggering result is still included.
        """
        ...
