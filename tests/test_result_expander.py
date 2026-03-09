"""Unit tests for ResultExpander.expand()."""
from datetime import date, datetime

import pytest

from src.application.services.result_expander import ResultExpander
from src.domain.models.provider import BookingProvider
from src.domain.models.result import BookingResult, Car, Rate
from src.domain.models.search import BookingSearch, Location
from src.domain.models.season import HomogeneousZone

_PROVIDER = BookingProvider(name="Provider A", base_url="https://example.com")
_LOC = Location(canonical_id="ALC", display_name="Alicante Airport")


def _search(pickup: date, days: int) -> BookingSearch:
    dt = datetime(pickup.year, pickup.month, pickup.day, 10, 0)
    dropoff = datetime(pickup.year, pickup.month, pickup.day + days, 10, 0)
    return BookingSearch(
        provider=_PROVIDER,
        pickup_location=_LOC,
        dropoff_location=_LOC,
        pickup_at=dt,
        dropoff_at=dropoff,
    )


def _result(*groups: str, total: float = 100.0) -> BookingResult:
    cars = [
        Car(model="Model", group=g, description="",
            rates=[Rate(name="Std", currency="EUR", total=total, daily_price=total / 7)])
        for g in groups
    ]
    return BookingResult(provider=_PROVIDER, cars=cars)


def _zone(start: date, end: date, rep: date, group: str = "A") -> HomogeneousZone:
    return HomogeneousZone(
        start_date=start, end_date=end,
        reference_price=100.0, car_group=group,
        representative_date=rep,
    )


class TestEmptyInput:
    def test_empty_pairs_returns_empty(self):
        result = ResultExpander().expand([], [], date(2026, 3, 1), date(2026, 3, 31), [7])
        assert result == []


class TestNoGaps:
    def test_does_not_add_synthetics_when_all_days_covered(self):
        start = date(2026, 3, 1)
        end = date(2026, 3, 8)
        pairs = [(s, r) for d in range(8) for s, r in [
            (_search(date(2026, 3, 1 + d), 7), _result("A"))
        ] if (date(2026, 3, 1 + d) + __import__('datetime').timedelta(days=7)) <= end]

        # Single real pair whose dropoff is within period
        real_search = _search(date(2026, 3, 1), 7)
        real_result = _result("A")
        pairs = [(real_search, real_result)]

        zone = _zone(date(2026, 3, 1), date(2026, 3, 8), date(2026, 3, 1))
        expanded = ResultExpander().expand(
            pairs, [("Provider A", [zone])], date(2026, 3, 1), date(2026, 3, 8), [7]
        )
        # Real pair already covers the one searchable day, no synthetics expected
        assert expanded[0] == (real_search, real_result)


class TestGapFilling:
    def test_fills_gap_day_with_representative_cars(self):
        rep_date = date(2026, 3, 1)
        gap_date = date(2026, 3, 5)
        period_end = date(2026, 3, 20)

        rep_search = _search(rep_date, 7)
        rep_result = _result("A", total=100.0)
        pairs = [(rep_search, rep_result)]

        zone = _zone(date(2026, 3, 1), date(2026, 3, 14), rep_date)
        expanded = ResultExpander().expand(
            pairs,
            [("Provider A", [zone])],
            period_start=date(2026, 3, 1),
            period_end=period_end,
            durations=[7],
        )

        synthetic_pairs = expanded[len(pairs):]
        assert any(
            s.pickup_at.date() == gap_date
            for s, _ in synthetic_pairs
        ), "Gap day should be filled with a synthetic pair"

    def test_synthetic_cars_belong_to_correct_group(self):
        rep_date = date(2026, 3, 1)
        period_end = date(2026, 3, 20)

        rep_search = _search(rep_date, 7)
        rep_result = _result("A", "B", total=100.0)
        pairs = [(rep_search, rep_result)]

        zone_a = _zone(date(2026, 3, 1), date(2026, 3, 14), rep_date, group="A")
        zone_b = _zone(date(2026, 3, 1), date(2026, 3, 14), rep_date, group="B")
        expanded = ResultExpander().expand(
            pairs,
            [("Provider A", [zone_a, zone_b])],
            period_start=date(2026, 3, 1),
            period_end=period_end,
            durations=[7],
        )

        for _, res in expanded[len(pairs):]:
            groups = {car.group for car in res.cars}
            assert groups.issubset({"A", "B"})

    def test_skips_gap_when_representative_result_missing(self):
        period_end = date(2026, 3, 20)
        # No pairs — representative result not in index
        zone = _zone(date(2026, 3, 1), date(2026, 3, 14), date(2026, 3, 1))
        expanded = ResultExpander().expand(
            [],
            [("Provider A", [zone])],
            period_start=date(2026, 3, 1),
            period_end=period_end,
            durations=[7],
        )
        assert expanded == []

    def test_does_not_fill_gap_beyond_period_end(self):
        rep_date = date(2026, 3, 1)
        period_end = date(2026, 3, 10)

        rep_search = _search(rep_date, 7)
        rep_result = _result("A")
        pairs = [(rep_search, rep_result)]

        zone = _zone(date(2026, 3, 1), date(2026, 3, 10), rep_date)
        expanded = ResultExpander().expand(
            pairs,
            [("Provider A", [zone])],
            period_start=date(2026, 3, 1),
            period_end=period_end,
            durations=[7],
        )

        for s, _ in expanded[len(pairs):]:
            assert s.dropoff_at.date() <= period_end, (
                f"Synthetic dropoff {s.dropoff_at.date()} exceeds period_end {period_end}"
            )

    def test_original_pairs_always_preserved(self):
        rep_search = _search(date(2026, 3, 1), 7)
        rep_result = _result("A")
        pairs = [(rep_search, rep_result)]

        zone = _zone(date(2026, 3, 1), date(2026, 3, 31), date(2026, 3, 1))
        expanded = ResultExpander().expand(
            pairs,
            [("Provider A", [zone])],
            period_start=date(2026, 3, 1),
            period_end=date(2026, 3, 31),
            durations=[7],
        )
        assert expanded[0] == (rep_search, rep_result)
