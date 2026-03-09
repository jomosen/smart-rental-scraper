"""Unit tests for PricePointExtractor.extract()."""
from datetime import date, datetime

import pytest

from src.application.smart_scraping.price_point_extractor import PricePointExtractor
from src.domain.models.provider import BookingProvider
from src.domain.models.result import BookingResult, Car, Rate
from src.domain.models.search import BookingSearch, Location

_PROVIDER = BookingProvider(name="Test", base_url="https://example.com")
_LOC = Location(canonical_id="ALC", display_name="Alicante Airport")


def _search(pickup: date, days: int = 7) -> BookingSearch:
    pickup_dt = datetime(pickup.year, pickup.month, pickup.day, 10, 0)
    return BookingSearch(
        provider=_PROVIDER,
        pickup_location=_LOC,
        dropoff_location=_LOC,
        pickup_at=pickup_dt,
        dropoff_at=pickup_dt.replace(day=pickup_dt.day + days),
    )


def _result(*cars: Car) -> BookingResult:
    return BookingResult(provider=_PROVIDER, cars=list(cars))


def _car(group: str, rate_name: str, total: float) -> Car:
    return Car(model="ModelX", group=group, description="",
               rates=[Rate(name=rate_name, currency="EUR", total=total, daily_price=total / 7)])


class TestBasicExtraction:
    def test_extracts_first_car_first_rate(self):
        extractor = PricePointExtractor()
        search = _search(date(2026, 3, 1))
        result = _result(_car("A", "Standard", 70.0))
        points = extractor.extract([search], [result])
        assert len(points) == 1
        assert points[0].total_price == 70.0
        assert points[0].car_group == "A"
        assert points[0].duration_days == 7

    def test_skips_empty_results(self):
        extractor = PricePointExtractor()
        search = _search(date(2026, 3, 1))
        empty = BookingResult(provider=_PROVIDER)
        points = extractor.extract([search], [empty])
        assert points == []

    def test_skips_none_results(self):
        extractor = PricePointExtractor()
        search = _search(date(2026, 3, 1))
        points = extractor.extract([search], [None])
        assert points == []


class TestRateFilter:
    def test_filters_by_rate_name(self):
        extractor = PricePointExtractor(rate_name="Premium")
        search = _search(date(2026, 3, 1))
        car = Car(
            model="ModelX", group="B", description="",
            rates=[
                Rate(name="Standard", currency="EUR", total=50.0, daily_price=7.14),
                Rate(name="Premium", currency="EUR", total=90.0, daily_price=12.86),
            ],
        )
        points = extractor.extract([search], [_result(car)])
        assert len(points) == 1
        assert points[0].total_price == 90.0

    def test_skips_car_when_rate_not_found(self):
        extractor = PricePointExtractor(rate_name="NonExistent")
        search = _search(date(2026, 3, 1))
        result = _result(_car("A", "Standard", 70.0))
        points = extractor.extract([search], [result])
        assert points == []

    def test_falls_back_to_next_car_when_first_lacks_rate(self):
        extractor = PricePointExtractor(rate_name="Premium")
        search = _search(date(2026, 3, 1))
        car_without = _car("A", "Standard", 50.0)
        car_with = _car("B", "Premium", 90.0)
        points = extractor.extract([search], [_result(car_without, car_with)])
        assert len(points) == 1
        assert points[0].car_group == "B"


class TestMultipleSearches:
    def test_extracts_one_point_per_valid_search(self):
        extractor = PricePointExtractor()
        searches = [_search(date(2026, 3, d)) for d in (1, 8, 15)]
        results = [_result(_car("A", "Std", 70.0 + d)) for d in range(3)]
        points = extractor.extract(searches, results)
        assert len(points) == 3

    def test_pickup_date_matches_search(self):
        extractor = PricePointExtractor()
        d = date(2026, 4, 10)
        points = extractor.extract([_search(d)], [_result(_car("A", "Std", 70.0))])
        assert points[0].pickup_date == d
