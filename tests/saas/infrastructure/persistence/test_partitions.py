"""Unit tests for the month-partition date maths (no DB required)."""
from datetime import date

from src.saas.infrastructure.persistence.partitions import (
    _first_of_month,
    _next_month,
)


def test_first_of_month_snaps_to_day_one():
    assert _first_of_month(date(2026, 7, 15)) == date(2026, 7, 1)
    assert _first_of_month(date(2026, 7, 1)) == date(2026, 7, 1)


def test_next_month_within_year():
    assert _next_month(date(2026, 7, 1)) == date(2026, 8, 1)
    assert _next_month(date(2026, 1, 31)) == date(2026, 2, 1)


def test_next_month_rolls_over_december():
    assert _next_month(date(2026, 12, 1)) == date(2027, 1, 1)
    assert _next_month(date(2026, 12, 15)) == date(2027, 1, 1)
