import re
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


def _parse_seats(text: str) -> Optional[int]:
    """Parses '5' → 5, '5+2' → 7 (foldable seats summed), or None on failure."""
    text = text.strip()
    if not text:
        return None
    parts = text.split("+")
    try:
        return sum(int(p.strip()) for p in parts if p.strip())
    except ValueError:
        return None


def _parse_luggage(text: str) -> Optional[int]:
    """Parses '2', '4' → int; returns None for volumes ('6m³') or empty."""
    text = text.strip()
    if re.fullmatch(r"\d+", text):
        return int(text)
    return None


class SolcarScraper(BaseScraper):
    """Concrete scraper for Solcar."""

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

    async def _refine_form(self, criteria: BookingSearch) -> None:
        await self._pause(2.0, 5.0)
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
            seats_el = item.select_one('[title="Número de plazas"]')
            luggage_el = item.select_one('[title="Capacidad maletero"]')
            trans_el = item.select_one('[title*="Cambio"]')

            example_models = model_el.get_text(strip=True) if model_el else ""
            group = group_el.get_text(strip=True).strip("()") if group_el else ""
            daily_price = _parse_price(daily_el.get_text()) if daily_el else Decimal(0)
            total = _parse_price(total_el.get_text()) if total_el else Decimal(0)
            seats = _parse_seats(seats_el.get_text(strip=True)) if seats_el else None
            luggage = _parse_luggage(luggage_el.get_text(strip=True)) if luggage_el else None
            transmission = None
            if trans_el:
                title = trans_el.get("title", "")
                transmission = "automatic" if "automático" in title else "manual"

            cars.append(Car(
                model=example_models,
                group=group,
                description="",
                example_models=example_models,
                seats=seats,
                luggage=luggage,
                transmission=transmission,
                rates=[Rate(
                    name="Standard",
                    currency="EUR",
                    total=total,
                    daily_price=daily_price,
                )],
            ))

        return BookingResult(provider_name=criteria.provider_name, cars=cars)
