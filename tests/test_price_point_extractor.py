"""Unit tests for PricePointExtractor.extract().

Contract (post-fix): one PricePoint per car with a valid rate, per search.
The old "first car is sufficient" assumption has been removed; all cars
that match the rate filter contribute a point.
"""
from datetime import date, datetime
from decimal import Decimal

import pytest

from src.scraper.application.smart_scraping.price_point_extractor import PricePointExtractor
from src.shared.domain.models.result import BookingResult, Car, Rate
from src.shared.domain.models.search import BookingSearch, Location

_LOC = Location(canonical_id="ALC", display_name="Alicante Airport")


def _search(pickup: date, days: int = 7) -> BookingSearch:
    pickup_dt = datetime(pickup.year, pickup.month, pickup.day, 10, 0)
    return BookingSearch(
        provider_name="Test",
        pickup_location=_LOC,
        dropoff_location=_LOC,
        pickup_at=pickup_dt,
        dropoff_at=pickup_dt.replace(day=pickup_dt.day + days),
    )


def _result(*cars: Car) -> BookingResult:
    return BookingResult(provider_name="Test", cars=list(cars))


def _car(group: str, rate_name: str, total: Decimal) -> Car:
    return Car(model="ModelX", group=group, description="", example_models="",
               rates=[Rate(name=rate_name, currency="EUR", total=total, daily_price=total / 7)])


class TestBasicExtraction:
    def test_single_car_yields_single_point(self):
        extractor = PricePointExtractor()
        search = _search(date(2026, 3, 1))
        result = _result(_car("A", "Standard", Decimal("70.0")))
        points = extractor.extract([search], [result])
        assert len(points) == 1
        assert points[0].total_price == 70.0
        assert points[0].car_group == "A"
        assert points[0].duration_days == 7

    def test_multiple_cars_all_extracted(self):
        """All cars with a valid rate must produce a point — not just the first."""
        extractor = PricePointExtractor()
        search = _search(date(2026, 3, 1))
        result = _result(
            _car("A", "Std", Decimal("70.0")),
            _car("C", "Std", Decimal("100.0")),
            _car("D", "Std", Decimal("120.0")),
        )
        points = extractor.extract([search], [result])
        assert len(points) == 3
        assert {p.car_group for p in points} == {"A", "C", "D"}

    def test_skips_empty_results(self):
        extractor = PricePointExtractor()
        search = _search(date(2026, 3, 1))
        empty = BookingResult(provider_name="Test")
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
            model="ModelX", group="B", description="", example_models="",
            rates=[
                Rate(name="Standard", currency="EUR", total=Decimal("50.0"), daily_price=Decimal("7.14")),
                Rate(name="Premium", currency="EUR", total=Decimal("90.0"), daily_price=Decimal("12.86")),
            ],
        )
        points = extractor.extract([search], [_result(car)])
        assert len(points) == 1
        assert points[0].total_price == 90.0

    def test_skips_car_when_rate_not_found(self):
        extractor = PricePointExtractor(rate_name="NonExistent")
        search = _search(date(2026, 3, 1))
        result = _result(_car("A", "Standard", Decimal("70.0")))
        points = extractor.extract([search], [result])
        assert points == []

    def test_car_without_matching_rate_is_skipped(self):
        """Cars that don't match the rate filter are skipped; others are included."""
        extractor = PricePointExtractor(rate_name="Premium")
        search = _search(date(2026, 3, 1))
        car_without = _car("A", "Standard", Decimal("50.0"))
        car_with = _car("B", "Premium", Decimal("90.0"))
        points = extractor.extract([search], [_result(car_without, car_with)])
        assert len(points) == 1
        assert points[0].car_group == "B"

    def test_rate_name_filters_each_car_independently(self):
        """With rate_name set, every car is checked individually; matching ones all yield points."""
        extractor = PricePointExtractor(rate_name="Premium")
        search = _search(date(2026, 3, 1))
        car_a = _car("A", "Premium", Decimal("90.0"))
        car_b = _car("B", "Standard", Decimal("60.0"))
        car_c = _car("C", "Premium", Decimal("110.0"))
        points = extractor.extract([search], [_result(car_a, car_b, car_c)])
        assert len(points) == 2
        assert {p.car_group for p in points} == {"A", "C"}


class TestMultipleSearches:
    def test_accumulates_points_across_searches(self):
        extractor = PricePointExtractor()
        searches = [_search(date(2026, 3, d)) for d in (1, 8, 15)]
        results = [_result(_car("A", "Std", Decimal(str(70.0 + d)))) for d in range(3)]
        points = extractor.extract(searches, results)
        assert len(points) == 3

    def test_pickup_date_matches_search(self):
        extractor = PricePointExtractor()
        d = date(2026, 4, 10)
        points = extractor.extract([_search(d)], [_result(_car("A", "Std", Decimal("70.0")))])
        assert points[0].pickup_date == d

    def test_car_groups_preserved_across_multi_car_searches(self):
        """Points from multi-car results preserve their car_group across searches."""
        extractor = PricePointExtractor()
        searches = [_search(date(2026, 3, 1)), _search(date(2026, 3, 8))]
        results = [
            _result(_car("A", "Std", Decimal("70.0")), _car("C", "Std", Decimal("100.0"))),
            _result(_car("A", "Std", Decimal("75.0")), _car("C", "Std", Decimal("105.0"))),
        ]
        points = extractor.extract(searches, results)
        assert len(points) == 4
        groups = [p.car_group for p in points]
        assert groups.count("A") == 2
        assert groups.count("C") == 2
