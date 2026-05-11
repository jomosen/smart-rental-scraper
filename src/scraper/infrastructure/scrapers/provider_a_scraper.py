import calendar
from datetime import datetime
from decimal import Decimal
from typing import Optional

from bs4 import BeautifulSoup, Tag

from ...domain.interfaces.driver import IBrowserDriver
from ...domain.models.booking_provider import BookingProvider
from ....shared.domain.models.search import BookingSearch
from ....shared.domain.models.result import BookingResult, Car, Rate
from .base_scraper import BaseScraper


def _round_minutes(minute: int) -> str:
    """Rounds down to the nearest quarter hour (the form only accepts 00/15/30/45)."""
    return str((minute // 15) * 15).zfill(2)


def _parse_daily_price(text: str) -> Decimal:
    """Converts '33,99 € / Día' → Decimal('33.99')"""
    clean = text.split("€")[0].strip().replace(",", ".")
    return Decimal(clean)


class ProviderAScraper(BaseScraper):
    """Concrete scraper for Provider A."""

    def __init__(self, driver: IBrowserDriver, provider: Optional[BookingProvider] = None) -> None:
        super().__init__(driver=driver, provider=provider)

    async def _submit_form(self, criteria: BookingSearch) -> None:
        await self._driver.navigate(self._provider.base_url)
        await self._pause(2.0, 4.0)

        # --- Pickup office ---
        await self._driver.select_option("#sel_city", criteria.pickup_location.canonical_id)
        await self._pause(1.0, 2.0)

        # --- Pickup date: open check-in datepicker and select the day ---
        await self._driver.click(".t-check-in")
        await self._driver.wait_for_selector(".t-datepicker-days")
        await self._select_date(criteria.pickup_at)
        await self._pause(1.0, 2.0)

        # --- Pickup time ---
        await self._driver.select_option("#pickupTime1", str(criteria.pickup_at.hour).zfill(2))
        await self._pause(0.8, 1.5)
        await self._driver.select_option("#pickupTime2", _round_minutes(criteria.pickup_at.minute))
        await self._pause(1.0, 2.0)

        # --- Return date: the check-out datepicker opens automatically ---
        await self._driver.wait_for_selector(".t-datepicker-days")
        await self._select_date(criteria.dropoff_at)
        await self._pause(1.0, 2.0)

        # --- Return time ---
        await self._driver.select_option("#pickupTime3", str(criteria.dropoff_at.hour).zfill(2))
        await self._pause(0.8, 1.5)
        await self._driver.select_option("#pickupTime4", _round_minutes(criteria.dropoff_at.minute))

        # --- Submit the form and wait for results ---
        await self._pause(2.0, 3.5)
        await self._driver.click(".bFindSolution")
        await self._driver.wait_for_selector(".ajax-cars", timeout=15000)

    async def _refine_form(self, criteria: BookingSearch) -> None:
        """Modifies dates from the results page without reloading the initial form."""
        await self._driver.scroll_to_top()
        await self._pause(1.5, 2.5)  # extra margin after scroll so the panel is stable
        if not await self._driver.element_exists(".t-check-in"):
            await self._driver.click(".modificar-fechas.modificar-fechas-page-extras")
            await self._driver.wait_for_selector(".t-check-in", timeout=15000)

        await self._driver.click(".t-check-in")
        await self._driver.wait_for_selector(".t-datepicker-days")
        await self._select_date(criteria.pickup_at)
        await self._pause(1.0, 2.0)

        await self._driver.wait_for_selector(".t-datepicker-days")
        await self._select_date(criteria.dropoff_at)
        await self._pause(2.0, 3.5)

        await self._driver.click(".bFindSolution")
        await self._driver.wait_for_selector(".ajax-cars", timeout=15000)

    async def _select_date(self, date: datetime) -> None:
        """Navigates the datepicker to the correct month and clicks the day."""
        midnight = date.replace(hour=0, minute=0, second=0, microsecond=0)
        timestamp_ms = calendar.timegm(midnight.timetuple()) * 1000
        selector = f'.t-datepicker-days td[data-t-date="{timestamp_ms}"]:not(.t-disabled)'

        while not await self._driver.element_exists(selector):
            await self._driver.click(".t-datepicker-days .t-arrow.t-next:not(.t-disabled)")

        await self._driver.click(selector)

    async def _extract_results(self, criteria: BookingSearch) -> BookingResult:
        await self._driver.wait_for_load_state("networkidle")
        html = await self._driver.get_page_source()
        soup = BeautifulSoup(html, "html.parser")

        cars = []
        item: Tag
        for item in soup.select(".item-car"):
            model_el = item.select_one(".title .modelo")
            group_el = item.select(".title p")

            model = model_el.get_text(strip=True) if model_el else "Desconocido"
            group = group_el[-1].get_text(strip=True) if group_el else ""

            descriptions = [
                el.get_text(strip=True)
                for el in item.select(".description.description-desktop")
            ]

            rates = []
            tarifa: Tag
            for tarifa in item.select(".tarifa-item"):
                name_el = tarifa.select_one(".tarifa-title")
                per_day_el = tarifa.select_one(".per-day")

                rates.append(Rate(
                    name=name_el.get_text(strip=True) if name_el else "",
                    currency="EUR",
                    total=Decimal(str(tarifa.get("data-value") or "0")),
                    daily_price=_parse_daily_price(per_day_el.get_text()) if per_day_el else Decimal(0),
                ))

            cars.append(Car(
                model=model,
                group=group,
                description=", ".join(descriptions),
                example_models="",
                rates=list(dict.fromkeys(rates)),
            ))

        return BookingResult(provider_name=criteria.provider_name, cars=cars)
