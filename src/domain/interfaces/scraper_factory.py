from typing import Protocol

from .scraper import IBookingScraper


class IScraperFactory(Protocol):
    """Contract that any scraper factory must fulfil."""

    def create(self, provider_name: str) -> IBookingScraper:
        ...
