from abc import ABC, abstractmethod
from typing import List

from .driver import IBrowserDriver
from ..models.result import BookingResult


class IBookingScraper(ABC):
    """
    Core contract for all booking scrapers.
    The only entry point is scrape_session(), which reuses the browser
    across multiple searches to minimise overhead.
    """

    def __init__(self, driver: IBrowserDriver) -> None:
        self.driver = driver

    @abstractmethod
    async def scrape_session(self, requests: list) -> List[BookingResult]:
        """Executes multiple searches reusing a single browser session."""
        ...