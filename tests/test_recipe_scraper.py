"""Unit tests for RecipeScraper — no Playwright, no DB required."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.scraper.infrastructure.scrapers.recipe_scraper import (
    RecipeScraper,
    _criteria_to_targets,
    _map_transmission,
    _map_vehicle,
)
from src.scraper.infrastructure.builder.extraction.models import VehicleResult
from src.shared.domain.models.search import BookingSearch, Location


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
