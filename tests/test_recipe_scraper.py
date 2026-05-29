"""Unit tests for RecipeScraper — no Playwright, no DB required."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.scraper.domain.builder.models import Recipe
from src.scraper.application.builder.build_recipe import recipe_from_dict, recipe_to_dict
from src.scraper.infrastructure.scrapers.recipe_scraper import (
    RecipeScraper,
    _criteria_to_targets,
    _map_transmission,
    _map_vehicle,
)
from src.scraper.infrastructure.builder.extraction.models import VehicleResult
from src.shared.domain.models.search import BookingSearch, Location


def _minimal_recipe(**overrides) -> Recipe:
    defaults = dict(
        provider_key="centauro",
        discovered_at="2026-01-01T00:00:00+00:00",
        url="https://centauro.net",
        cookies_strategy="adaptive_poll_heuristic",
        form_fields={},
        submit_selector="#search",
        submit_selector_type="css",
        card_source="mark_valid_cards",
        field_extractors=[],
        price_strategy="price_cascade",
    )
    defaults.update(overrides)
    return Recipe(**defaults)


def _make_criteria(days: int = 7) -> BookingSearch:
    pickup = datetime(2026, 7, 1, 10, 0)
    dropoff = datetime(2026, 7, 1 + days, 10, 0)
    loc = Location(canonical_id="ALC", display_name="Alicante")
    return BookingSearch(
        provider_name="centauro",
        pickup_location=loc,
        dropoff_location=loc,
        pickup_at=pickup,
        dropoff_at=dropoff,
    )


class TestMapTransmission:
    def test_manual_uppercase_m(self):
        assert _map_transmission("M") == "manual"

    def test_manual_full_word(self):
        assert _map_transmission("Manual") == "manual"

    def test_automatic_uppercase_a(self):
        assert _map_transmission("A") == "automatic"

    def test_automatic_full_word(self):
        assert _map_transmission("Automático") == "automatic"

    def test_none_returns_none(self):
        assert _map_transmission(None) is None


class TestMapVehicle:
    def test_basic_mapping(self):
        vr = VehicleResult(
            model="Fiat Panda",
            group_code="MDMR",
            transmission="M",
            seats=5,
            price_final=140.0,
            currency="EUR",
        )
        car = _map_vehicle(vr, _make_criteria(7))

        assert car.model == "Fiat Panda"
        assert car.group == "MDMR"
        assert car.description == ""
        assert car.example_models == "Fiat Panda"
        assert car.seats == 5
        assert car.luggage is None
        assert car.transmission == "manual"
        assert len(car.rates) == 1
        rate = car.rates[0]
        assert rate.name == "default"
        assert rate.currency == "EUR"
        assert rate.total == Decimal("140.0")
        assert rate.daily_price == Decimal("20.0")

    def test_automatic_transmission(self):
        vr = VehicleResult(model="Toyota Yaris", group_code="ECAR", transmission="A", price_final=200.0, currency="EUR")
        car = _map_vehicle(vr, _make_criteria(5))
        assert car.transmission == "automatic"

    def test_daily_price_calculation(self):
        vr = VehicleResult(model="X", group_code="Y", price_final=300.0, currency="EUR")
        car = _map_vehicle(vr, _make_criteria(3))
        assert car.rates[0].daily_price == Decimal("100.0")

    def test_currency_defaults_to_eur(self):
        vr = VehicleResult(model="X", group_code="Y", price_final=100.0, currency=None)
        car = _map_vehicle(vr, _make_criteria(4))
        assert car.rates[0].currency == "EUR"

    def test_none_model_and_group_become_empty_string(self):
        vr = VehicleResult(model=None, group_code=None, price_final=50.0)
        car = _map_vehicle(vr, _make_criteria(1))
        assert car.model == ""
        assert car.group == ""
        assert car.example_models == ""

    def test_single_day_no_division_by_zero(self):
        vr = VehicleResult(model="X", group_code="Y", price_final=60.0, currency="EUR")
        car = _map_vehicle(vr, _make_criteria(1))
        assert car.rates[0].daily_price == Decimal("60.0")


class TestCriteriaToTargets:
    def test_derived_targets(self):
        criteria = _make_criteria(7)
        targets = _criteria_to_targets(criteria)

        assert targets["location"] == "Alicante"
        assert str(targets["pickup_date"]) == "2026-07-01"
        assert str(targets["return_date"]) == "2026-07-08"
        assert targets["pickup_time"] == "10:00"
        assert targets["return_time"] == "10:00"


class TestRecipeSerialization:
    def test_to_dict_emits_refine_fields(self):
        recipe = _minimal_recipe(
            refine_url="https://centauro.net/reserva/lugar-y-fechas/",
            refine_strategy="navigate_and_change_dates",
        )
        d = recipe_to_dict(recipe)
        assert d["refine_url"] == "https://centauro.net/reserva/lugar-y-fechas/"
        assert d["refine_strategy"] == "navigate_and_change_dates"

    def test_to_dict_emits_defaults(self):
        recipe = _minimal_recipe()
        d = recipe_to_dict(recipe)
        assert d["refine_url"] is None
        assert d["refine_strategy"] == "none"

    def test_from_dict_old_recipe_missing_fields_uses_defaults(self):
        old = {
            "provider_key": "centauro",
            "discovered_at": "2025-01-01T00:00:00",
            "url": "https://centauro.net",
            "form_fields": {},
            "submit_selector": "#s",
            "field_extractors": [],
        }
        recipe = recipe_from_dict(old)
        assert recipe.refine_url is None
        assert recipe.refine_strategy == "none"

    def test_from_dict_reads_refine_fields(self):
        d = {
            "provider_key": "centauro",
            "discovered_at": "",
            "url": "https://centauro.net",
            "form_fields": {},
            "submit_selector": "#s",
            "field_extractors": [],
            "refine_url": "https://centauro.net/reserva/",
            "refine_strategy": "navigate_and_change_dates",
        }
        recipe = recipe_from_dict(d)
        assert recipe.refine_url == "https://centauro.net/reserva/"
        assert recipe.refine_strategy == "navigate_and_change_dates"

    def test_roundtrip_preserves_refine_fields(self):
        original = _minimal_recipe(
            refine_url="https://centauro.net/reserva/",
            refine_strategy="navigate_and_change_dates",
        )
        restored = recipe_from_dict(recipe_to_dict(original))
        assert restored.refine_url == original.refine_url
        assert restored.refine_strategy == original.refine_strategy


class TestRecipeScraperRefineDispatch:
    def _scraper_with_recipe(self, strategy: str) -> RecipeScraper:
        scraper = RecipeScraper(driver=MagicMock(), provider_id=1)
        scraper._session = MagicMock()
        scraper._recipe = _minimal_recipe(
            refine_url="https://centauro.net/reserva/",
            refine_strategy=strategy,
        )
        return scraper

    async def test_none_strategy_delegates_to_submit_form(self):
        scraper = self._scraper_with_recipe("none")
        scraper._submit_form = AsyncMock()
        criteria = _make_criteria()
        await scraper._refine_form(criteria)
        scraper._submit_form.assert_called_once_with(criteria)

    async def test_no_session_delegates_to_submit_form(self):
        scraper = RecipeScraper(driver=MagicMock(), provider_id=1)
        scraper._session = None
        scraper._submit_form = AsyncMock()
        criteria = _make_criteria()
        await scraper._refine_form(criteria)
        scraper._submit_form.assert_called_once_with(criteria)

    async def test_navigate_strategy_calls_refine_dates(self):
        from src.scraper.infrastructure.builder.scraper_engine import ScrapeResult

        scraper = self._scraper_with_recipe("navigate_and_change_dates")
        mock_result = MagicMock(spec=ScrapeResult)
        mock_result.success = True

        with patch(
            "src.scraper.infrastructure.scrapers.recipe_scraper.refine_dates",
            new=AsyncMock(return_value=mock_result),
        ) as mock_refine:
            await scraper._refine_form(_make_criteria())

        mock_refine.assert_called_once()
        assert scraper._last_result is mock_result

    async def test_failed_refine_dates_raises_runtime_error(self):
        from src.scraper.infrastructure.builder.scraper_engine import ScrapeResult

        scraper = self._scraper_with_recipe("navigate_and_change_dates")
        mock_result = MagicMock(spec=ScrapeResult)
        mock_result.success = False
        mock_result.failed_phase = "submit"
        mock_result.error = "timeout"

        with patch(
            "src.scraper.infrastructure.scrapers.recipe_scraper.refine_dates",
            new=AsyncMock(return_value=mock_result),
        ):
            with pytest.raises(RuntimeError, match="refine_dates failed"):
                await scraper._refine_form(_make_criteria())


class TestRecipeScraperConstruction:
    def test_instantiates_without_real_driver(self):
        driver = MagicMock()
        scraper = RecipeScraper(driver=driver, provider_id=42, session_factory=None)

        assert scraper._provider_id == 42
        assert scraper._session_factory is None
        assert scraper._recipe is None
        assert scraper._last_result is None

    def test_null_driver_replaces_injected_driver(self):
        from src.scraper.infrastructure.scrapers.recipe_scraper import _NullDriver

        real_driver = MagicMock()
        scraper = RecipeScraper(driver=real_driver, provider_id=1)
        assert isinstance(scraper._driver, _NullDriver)

    def test_registered_in_scraper_registry(self):
        from src.scraper.presentation.cli.container import SCRAPER_REGISTRY

        assert "centauro" in SCRAPER_REGISTRY
        assert SCRAPER_REGISTRY["centauro"] is RecipeScraper
