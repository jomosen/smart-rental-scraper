"""URL-based Victoria scraper.

Navigates directly to the results URL instead of filling the search form,
cutting per-search latency significantly (no Materialize dropdowns, no
#btnBuscar, no #btnVolverBuscar).

Drop-in replacement for VictoriaScraper: inherits _extract_results,
_parse_victoria_html, _close_msgbox_if_open and all parsing helpers unchanged.

Activation: set the provider row's scraper_key to "victoria_url" in the DB.
"""
from __future__ import annotations

from urllib.parse import urlencode, urlparse

from ....shared.domain.models.search import BookingSearch
from .victoria_scraper import VictoriaScraper

# Note: provider_locations.location_code for Victoria stores Victoria's own
# numeric code ("2" = Alicante Airport), so canonical_id is used directly as
# the oev/orv parameter without any extra mapping.


class VictoriaUrlScraper(VictoriaScraper):
    """Replaces form interaction with direct URL navigation.

    The results page is identical regardless of how the search was triggered,
    so all parsing logic from VictoriaScraper applies without modification.
    """

    def _build_search_url(self, criteria: BookingSearch) -> str:
        # provider_locations.location_code already stores Victoria's internal
        # numeric code (e.g. "2" for Alicante Airport), so canonical_id == oev.
        parsed = urlparse(self._provider.base_url)
        endpoint = f"{parsed.scheme}://{parsed.netloc}/vehiculos.php"

        params = {
            "oev":            criteria.pickup_location.canonical_id,
            "oevtxt":         criteria.pickup_location.display_name.upper(),
            "f1":             criteria.pickup_at.strftime("%d/%m/%Y %H:%M"),
            "orv":            criteria.dropoff_location.canonical_id,
            "orvtxt":         criteria.dropoff_location.display_name.upper(),
            "f2":             criteria.dropoff_at.strftime("%d/%m/%Y %H:%M"),
            "afiliado":       "0",
            "idioma":         "es",
            "edad":           "21",
            "promo":          "PROMO",
            "grupo":          "",
            "tipovehiculo":   "",
            "nombrevehiculo": "",
            "color_afiliado": "",
            "emp":            "victoriacars",
        }
        return endpoint + "?" + urlencode(params)

    # Wait for either a car card or the "no prices" modal — whichever appears
    # first signals that the AJAX response has been rendered.
    _RESULTS_READY = ".package-template.ficha, #msgbox.open"

    async def _submit_form(self, criteria: BookingSearch) -> None:
        url = self._build_search_url(criteria)
        await self._driver.navigate(url)
        await self._accept_cookies_if_present()
        await self._driver.wait_for_selector(self._RESULTS_READY, timeout=30000, state="attached")

    async def _refine_form(self, criteria: BookingSearch) -> None:
        # Direct navigation supersedes the "back to form" round-trip entirely.
        # No cookie banner on subsequent navigations within the same session.
        await self._pause(2.0, 5.0)
        url = self._build_search_url(criteria)
        await self._driver.navigate(url)
        await self._driver.wait_for_selector(self._RESULTS_READY, timeout=30000, state="attached")
