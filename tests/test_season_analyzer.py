"""Unit tests for SeasonAnalyzer.detect_zones()."""
from datetime import date
from decimal import Decimal

import pytest

from src.scraper.application.smart_scraping.season_analyzer import SeasonAnalyzer
from src.scraper.domain.models.season_internals import PricePoint


def _point(pickup: date, total: Decimal, duration: int = 7, group: str = "A") -> PricePoint:
    return PricePoint(pickup_date=pickup, duration_days=duration, total_price=total, car_group=group)


START = date(2026, 3, 1)
END   = date(2026, 5, 31)


class TestNoChangeDetected:
    def test_single_zone_when_prices_are_stable(self):
        analyzer = SeasonAnalyzer(price_change_threshold=0.05)
        points = [
            _point(date(2026, 3, 1), Decimal("70.0")),
            _point(date(2026, 3, 8), Decimal("71.0")),
            _point(date(2026, 3, 15), Decimal("70.5")),
        ]
        zones = analyzer.detect_zones(points, START, END, "A")
        assert len(zones) == 1
        assert zones[0].start_date == START
        assert zones[0].end_date == END

    def test_no_price_points_returns_single_zone_with_zero_price(self):
        analyzer = SeasonAnalyzer()
        zones = analyzer.detect_zones([], START, END, "A")
        assert len(zones) == 1
        assert zones[0].reference_price == Decimal(0)
        assert zones[0].start_date == START
        assert zones[0].end_date == END


class TestBoundaryDetection:
    def test_detects_single_price_jump(self):
        analyzer = SeasonAnalyzer(price_change_threshold=0.05)
        points = [
            _point(date(2026, 3, 1), Decimal("70.0")),
            _point(date(2026, 3, 8), Decimal("70.0")),
            _point(date(2026, 3, 15), Decimal("105.0")),  # +50% → boundary
            _point(date(2026, 3, 22), Decimal("104.0")),
        ]
        zones = analyzer.detect_zones(points, START, END, "A")
        assert len(zones) == 2
        assert zones[0].end_date < zones[1].start_date

    def test_detects_two_boundaries(self):
        analyzer = SeasonAnalyzer(price_change_threshold=0.05)
        points = [
            _point(date(2026, 3, 1),  Decimal("70.0")),
            _point(date(2026, 3, 8),  Decimal("140.0")),  # +100%
            _point(date(2026, 3, 15), Decimal("70.0")),   # −50%
        ]
        zones = analyzer.detect_zones(points, START, END, "A")
        assert len(zones) == 3

    def test_ignores_change_below_threshold(self):
        analyzer = SeasonAnalyzer(price_change_threshold=0.10)
        points = [
            _point(date(2026, 3, 1), Decimal("100.0")),
            _point(date(2026, 3, 8), Decimal("105.0")),  # +5% < 10% threshold
        ]
        zones = analyzer.detect_zones(points, START, END, "A")
        assert len(zones) == 1


class TestRepresentativeDate:
    def test_first_strategy_uses_zone_start(self):
        analyzer = SeasonAnalyzer(price_change_threshold=0.05, representative="first")
        points = [
            _point(date(2026, 3, 1), Decimal("70.0")),
            _point(date(2026, 3, 8), Decimal("140.0")),
        ]
        zones = analyzer.detect_zones(points, START, END, "A")
        assert zones[0].representative_date == zones[0].start_date

    def test_middle_strategy_uses_midpoint(self):
        analyzer = SeasonAnalyzer(price_change_threshold=0.05, representative="middle")
        points = [_point(date(2026, 3, 1), Decimal("70.0"))]
        zones = analyzer.detect_zones(points, START, END, "A")
        mid = START + (END - START) // 2
        assert zones[0].representative_date == mid


class TestGroupIsolation:
    def test_only_processes_matching_group(self):
        analyzer = SeasonAnalyzer(price_change_threshold=0.05)
        points = [
            _point(date(2026, 3, 1),  Decimal("70.0"),  group="A"),
            _point(date(2026, 3, 8),  Decimal("140.0"), group="B"),  # price jump but group B
        ]
        zones_a = analyzer.detect_zones(points, START, END, "A")
        assert len(zones_a) == 1  # no boundary visible in group A

    def test_zone_days_property(self):
        analyzer = SeasonAnalyzer()
        points = [_point(date(2026, 3, 1), Decimal("70.0"))]
        zones = analyzer.detect_zones(points, START, END, "A")
        expected_days = (END - START).days + 1
        assert zones[0].days == expected_days


class TestDetectZonesProviderLevel:
    def test_detect_zones_provider_level_aggregates_all_groups(self):
        # Group A jumps from 70 to 140 at Mar 8 (+100%), group B stays stable at 50.
        # Averaged daily prices: Mar1=(10+7.14)/2≈8.57, Mar8=(20+7.14)/2≈13.57 → +58% > 5%
        analyzer = SeasonAnalyzer(price_change_threshold=0.05)
        points = [
            _point(date(2026, 3, 1), Decimal("70.0"), group="A"),
            _point(date(2026, 3, 1), Decimal("50.0"), group="B"),
            _point(date(2026, 3, 8), Decimal("140.0"), group="A"),
            _point(date(2026, 3, 8), Decimal("50.0"), group="B"),
        ]
        zones = analyzer.detect_zones_provider_level(points, START, END)
        assert len(zones) == 2  # boundary detected between Mar 1 and Mar 8

    def test_detect_zones_provider_level_returns_single_zone_when_empty(self):
        analyzer = SeasonAnalyzer()
        zones = analyzer.detect_zones_provider_level([], START, END)
        assert len(zones) == 1
        assert zones[0].start_date == START
        assert zones[0].end_date == END

    def test_detect_zones_provider_level_assigns_empty_car_group(self):
        analyzer = SeasonAnalyzer(price_change_threshold=0.05)
        points = [
            _point(date(2026, 3, 1), Decimal("70.0"), group="Economy"),
            _point(date(2026, 3, 8), Decimal("70.0"), group="Economy"),
        ]
        zones = analyzer.detect_zones_provider_level(points, START, END)
        assert all(z.car_group == "" for z in zones)
