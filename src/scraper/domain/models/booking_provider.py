from dataclasses import dataclass


@dataclass(frozen=True)
class BookingProvider:
    """
    Scraper-layer config for a provider: the name used as key and the
    base URL the scraper navigates to. Not the same as the 'providers'
    catalog entity in the SaaS (which adds currency, status, scraper_key,
    etc.). The SaaS never consumes BookingProvider directly.
    """
    name: str
    base_url: str
    code: str = ""
