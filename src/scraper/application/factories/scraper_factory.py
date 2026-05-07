from ...domain.interfaces.scraper import IBookingScraper
from ...domain.interfaces.driver import IBrowserDriver
from ...domain.models.booking_provider import BookingProvider


class ScraperFactory:
    """
    Factory that creates concrete scrapers based on the provider name.

    OCP: to add a new provider, pass it in the registry dict — no class modification needed.
    DIP: receives driver_class by injection, not as a concrete instance.
    No shared mutable state: each factory instance owns its registry.
    """

    def __init__(
        self,
        registry: dict[str, type[IBookingScraper]],
        driver_class: type[IBrowserDriver],
        provider_configs: dict[str, BookingProvider] | None = None,
        scraper_kwargs: dict | None = None,
    ) -> None:
        self._registry = {k.lower(): v for k, v in registry.items()}
        self._driver_class = driver_class
        self._provider_configs = {k.lower(): v for k, v in (provider_configs or {}).items()}
        self._scraper_kwargs = scraper_kwargs or {}

    def create(self, provider_name: str) -> IBookingScraper:
        """
        Returns an instance of the scraper corresponding to the provider.

        Raises:
            ValueError: if the provider is not registered.
        """
        key = provider_name.lower()
        scraper_class = self._registry.get(key)
        if scraper_class is None:
            available = ", ".join(self._registry.keys()) or "none"
            raise ValueError(
                f"Provider '{provider_name}' not registered. "
                f"Available: {available}"
            )
        provider = self._provider_configs.get(key)
        return scraper_class(driver=self._driver_class(), provider=provider, **self._scraper_kwargs)
