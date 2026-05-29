from decimal import Decimal
from typing import Optional

from bs4 import BeautifulSoup, Tag

from ...domain.interfaces.driver import IBrowserDriver
from ...domain.models.booking_provider import BookingProvider
from ....shared.domain.models.search import BookingSearch
from ....shared.domain.models.result import BookingResult, Car, Rate
from .base_scraper import BaseScraper


def _round_minutes_30(minute: int) -> str:
    """Rounds down to the nearest 30-minute interval (the form only accepts 00/30)."""
    return str((minute // 30) * 30).zfill(2)


def _parse_price(text: str) -> Decimal:
    """Converts '32,86 € ', '(230,00 €)' or '1.100,04 €' → Decimal."""
    clean = text.replace("(", "").replace(")", "").split("€")[0].strip()
    # Remove thousands separator (dot) before converting the decimal comma
    clean = clean.replace(".", "").replace(",", ".")
    try:
        return Decimal(clean)
    except Exception:
        return Decimal(0)


def _parse_int(text: str) -> Optional[int]:
    """Parses a plain integer string; returns None on failure or empty input."""
    try:
        return int(text.strip())
    except (ValueError, AttributeError):
        return None


class VictoriaScraper(BaseScraper):
    """Concrete scraper for Victoria."""

    def __init__(self, driver: IBrowserDriver, provider: Optional[BookingProvider] = None) -> None:
        super().__init__(driver=driver, provider=provider)

    _MATERIALIZE_TRIGGER = "input.select-dropdown[data-activates]"

    async def _select_materialize(self, nth: int, option_text: str) -> None:
        """Opens the Nth Materialize custom select and selects the option by exact text."""
        trigger_selector = f"{self._MATERIALIZE_TRIGGER} >> nth={nth}"
        dropdown_id = await self._driver.get_attribute(trigger_selector, "data-activates")
        await self._pause(1.0, 2.0)
        await self._driver.click(trigger_selector)
        await self._driver.wait_for_selector(f"#{dropdown_id}")
        await self._pause(0.8, 1.5)
        await self._driver.click(f"#{dropdown_id} li span:text-is(\"{option_text}\")")

    async def _fill_form(self, criteria: BookingSearch) -> None:
        """Fills in the search form fields (without navigating or submitting)."""
        await self._select_materialize(0, criteria.pickup_location.display_name)
        await self._select_materialize(1, criteria.dropoff_location.display_name)

        pickup_date = criteria.pickup_at.strftime("%d/%m/%Y")
        await self._pause(1.0, 2.0)
        await self._driver.set_input_value("#f1", pickup_date)

        pickup_time = f"{criteria.pickup_at.hour:02d}:{_round_minutes_30(criteria.pickup_at.minute)}"
        await self._select_materialize(2, pickup_time)

        dropoff_date = criteria.dropoff_at.strftime("%d/%m/%Y")
        await self._pause(1.0, 2.0)
        await self._driver.set_input_value("#f2", dropoff_date)

        dropoff_time = f"{criteria.dropoff_at.hour:02d}:{_round_minutes_30(criteria.dropoff_at.minute)}"
        await self._select_materialize(3, dropoff_time)

    async def _accept_cookies_if_present(self) -> None:
        try:
            await self._driver.wait_for_selector("#allow_all_cookies", timeout=8000)
            await self._driver.js_click("#allow_all_cookies")
            await self._pause(1.0, 2.0)
        except Exception:
            pass

    async def _refine_form(self, criteria: BookingSearch) -> None:
        """Returns to the form via btnVolverBuscar, fills and submits without opening a new tab."""
        await self._driver.scroll_to_top()
        await self._pause(1.0, 2.0)
        await self._driver.click("#btnVolverBuscar")
        await self._pause(2.0, 4.0)
        await self._fill_form(criteria)
        await self._pause(2.0, 3.5)
        await self._driver.click("#btnBuscar")
        await self._accept_cookies_if_present()
        await self._driver.wait_for_selector("#cont_vehiculos", timeout=20000, state="attached")

    async def _submit_form(self, criteria: BookingSearch) -> None:
        await self._driver.navigate(self._provider.base_url)
        await self._pause(2.0, 4.0)
        await self._fill_form(criteria)

        await self._pause(2.0, 3.5)
        try:
            # Try the historic behaviour: button opens results in a new tab.
            # Short timeout so we don't wait 30 s if the site navigates in-place.
            await self._driver.click_and_switch_tab("#btnBuscar", timeout=5000)
        except Exception:
            # The click already fired; site navigated in-place rather than
            # opening a new tab.  Results are loading on the current page.
            pass
        await self._accept_cookies_if_present()
        await self._driver.wait_for_selector("#cont_vehiculos", timeout=20000, state="attached")

    async def _extract_results(self, criteria: BookingSearch) -> BookingResult:
        html = await self._driver.get_page_source()
        soup = BeautifulSoup(html, "html.parser")

        cars = []
        item: Tag
        for item in soup.select("div.package-template.ficha"):
            model_el = item.select_one("span.nombregrupo")
            group_el = item.select_one("span.grupo")
            package_el = item.select_one(".package-header h5")
            daily_el = item.select_one("div.package-price div strong.victoriacarscolor-text")
            total_el = item.select_one("strong.discount-prize") or item.select_one("strong.precio_tot")
            seats_el = item.select_one(".icono_container.persona .valor")
            luggage_el = item.select_one(".icono_container.maleta .valor")

            model = model_el.get_text(strip=True) if model_el else "Desconocido"
            group = group_el.get_text(strip=True) if group_el else ""
            package_name = package_el.get_text(strip=True) if package_el else ""
            daily_price = _parse_price(daily_el.get_text()) if daily_el else Decimal(0)
            total = _parse_price(total_el.get_text()) if total_el else Decimal(0)
            seats = _parse_int(seats_el.get_text(strip=True)) if seats_el else None
            luggage = _parse_int(luggage_el.get_text(strip=True)) if luggage_el else None
            if item.select_one(".icono_container.manual"):
                transmission = "manual"
            elif item.select_one(".icono_container.automatico"):
                transmission = "automatic"
            else:
                transmission = None

            cars.append(Car(
                model=model,
                group=group,
                description="",
                example_models=model,
                seats=seats,
                luggage=luggage,
                transmission=transmission,
                rates=[Rate(
                    name=package_name,
                    currency="EUR",
                    total=total,
                    daily_price=daily_price,
                )],
            ))

        return BookingResult(provider_name=criteria.provider_name, cars=cars)
