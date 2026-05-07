from decimal import Decimal
from typing import Optional
from urllib.parse import quote

from bs4 import BeautifulSoup, Tag

from ...domain.interfaces.driver import IBrowserDriver
from ...domain.models.booking_provider import BookingProvider
from ....shared.domain.models.search import BookingSearch
from ....shared.domain.models.result import BookingResult, Car, Rate
from .base_scraper import BaseScraper


def _parse_price(text: str) -> Decimal:
    """Converts '26,68€/día', '355,121€ total', '1.227,6€ total' → Decimal."""
    clean = text.split("€")[0].strip()
    clean = clean.replace(".", "").replace(",", ".")
    try:
        return Decimal(clean)
    except Exception:
        return Decimal(0)


class ProviderCScraper(BaseScraper):
    """Concrete scraper for Provider C."""

    def __init__(self, driver: IBrowserDriver, provider: Optional[BookingProvider] = None) -> None:
        super().__init__(driver=driver, provider=provider)

    def _build_url(self, criteria: BookingSearch) -> str:
        fr = quote(criteria.pickup_at.strftime("%d-%m-%Y %H:%M"))
        fd = quote(criteria.dropoff_at.strftime("%d-%m-%Y %H:%M"))
        return (
            f"{self._provider.base_url}"
            f"?ofr={criteria.pickup_location.canonical_id}"
            f"&ofd"
            f"&fr={fr}"
            f"&fd={fd}"
        )

    async def _submit_form(self, criteria: BookingSearch) -> None:
        await self._driver.navigate(self._build_url(criteria))
        await self._driver.wait_for_selector(".vehiculo-item", timeout=15000)

    async def _extract_results(self, criteria: BookingSearch) -> BookingResult:
        await self._driver.wait_for_load_state("networkidle")
        html = await self._driver.get_page_source()
        soup = BeautifulSoup(html, "html.parser")

        cars = []
        item: Tag
        for item in soup.select(".vehiculo-item"):
            if "disabled" in item.get("class", []):
                continue

            model_el = item.select_one("h3.brxe-sjwbok")
            group_el = item.select_one(".brxe-xvavgc")
            daily_el = item.select_one(".brxe-mxprcd")
            total_el = item.select_one(".brxe-frerph")

            model = model_el.get_text(strip=True) if model_el else "Desconocido"
            group = group_el.get_text(strip=True).strip("()") if group_el else ""
            daily_price = _parse_price(daily_el.get_text()) if daily_el else Decimal(0)
            total = _parse_price(total_el.get_text()) if total_el else Decimal(0)

            cars.append(Car(
                model=model,
                group=group,
                description="",
                rates=[Rate(
                    name="Standard",
                    currency="EUR",
                    total=total,
                    daily_price=daily_price,
                )],
            ))

        return BookingResult(provider_name=criteria.provider_name, cars=cars)
