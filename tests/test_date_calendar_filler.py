"""Unit tests for DateCalendarFiller helpers.

Tests cover _is_day_cell_excluded, which guards against clicking day cells
from neighboring months in range-calendar double views (react-calendar
uses the --neighboringMonth class suffix for overflow days).
"""
from __future__ import annotations

import pytest

from src.scraper.infrastructure.builder.filling.fillers.date_calendar_filler import (
    _is_day_cell_excluded,
    _parse_month_year,
)


# ---------------------------------------------------------------------------
# _is_day_cell_excluded
# ---------------------------------------------------------------------------

class TestIsDayCellExcluded:
    """_is_day_cell_excluded returns True for any cell that must be skipped."""

    # -- aria-disabled --
    def test_aria_disabled_true_is_excluded(self):
        assert _is_day_cell_excluded("true", None, "react-calendar__tile") is True

    def test_aria_disabled_false_not_excluded(self):
        assert _is_day_cell_excluded("false", None, "react-calendar__tile") is False

    def test_aria_disabled_empty_not_excluded(self):
        assert _is_day_cell_excluded("", None, "react-calendar__tile") is False

    # -- disabled attribute --
    def test_disabled_attr_present_is_excluded(self):
        assert _is_day_cell_excluded("", "", "react-calendar__tile") is True

    def test_disabled_attr_none_not_excluded(self):
        assert _is_day_cell_excluded("", None, "react-calendar__tile") is False

    # -- class-name keywords --
    def test_neighboringmonth_class_is_excluded(self):
        """react-calendar overflow days carry --neighboringMonth; must be skipped."""
        cls = (
            "react-calendar__tile "
            "react-calendar__month-view__days__day "
            "react-calendar__month-view__days__day--neighboringMonth"
        )
        assert _is_day_cell_excluded("", None, cls) is True

    def test_neighboringmonth_class_case_insensitive(self):
        assert _is_day_cell_excluded("", None, "tile--NeighboringMonth") is True

    def test_disabled_class_is_excluded(self):
        assert _is_day_cell_excluded("", None, "tile tile--disabled") is True

    def test_outside_class_is_excluded(self):
        assert _is_day_cell_excluded("", None, "tile outside-month") is True

    def test_unavailable_class_is_excluded(self):
        assert _is_day_cell_excluded("", None, "day unavailable") is True

    def test_blocked_class_is_excluded(self):
        assert _is_day_cell_excluded("", None, "day blocked") is True

    def test_regular_day_class_not_excluded(self):
        """A plain selectable day must not be excluded."""
        cls = (
            "react-calendar__tile "
            "react-calendar__month-view__days__day"
        )
        assert _is_day_cell_excluded("", None, cls) is False

    def test_weekend_class_not_excluded(self):
        cls = (
            "react-calendar__tile "
            "react-calendar__month-view__days__day "
            "react-calendar__month-view__days__day--weekend"
        )
        assert _is_day_cell_excluded("", None, cls) is False


# ---------------------------------------------------------------------------
# _parse_month_year
# ---------------------------------------------------------------------------

class TestParseMonthYear:
    def test_spanish_format(self):
        assert _parse_month_year("agosto de 2026") == (2026, 8)

    def test_spanish_september(self):
        assert _parse_month_year("septiembre de 2026") == (2026, 9)

    def test_english_format(self):
        assert _parse_month_year("August 2026") == (2026, 8)

    def test_numeric_slash(self):
        assert _parse_month_year("08/2026") == (2026, 8)

    def test_empty_returns_none(self):
        assert _parse_month_year("") is None

    def test_garbage_returns_none(self):
        assert _parse_month_year("no date here") is None
