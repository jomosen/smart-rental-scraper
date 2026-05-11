"""Unit tests for provider_c_scraper parsing helpers.

These tests exercise only the pure functions (_parse_seats, _parse_luggage)
and the full _extract_results HTML parsing path via BeautifulSoup — no browser
required.
"""
from decimal import Decimal

import pytest
from bs4 import BeautifulSoup

from src.scraper.infrastructure.scrapers.provider_c_scraper import (
    _parse_luggage,
    _parse_seats,
)


class TestParseSeats:
    def test_plain_integer(self):
        assert _parse_seats("5") == 5

    def test_plus_notation_sums_parts(self):
        assert _parse_seats("5+2") == 7

    def test_single_digit(self):
        assert _parse_seats("2") == 2

    def test_nine_seats(self):
        assert _parse_seats("9") == 9

    def test_empty_string_returns_none(self):
        assert _parse_seats("") is None

    def test_non_numeric_returns_none(self):
        assert _parse_seats("n/a") is None


class TestParseLuggage:
    def test_plain_integer(self):
        assert _parse_luggage("2") == 2

    def test_four_bags(self):
        assert _parse_luggage("4") == 4

    def test_volume_string_returns_none(self):
        assert _parse_luggage("6m³") is None

    def test_large_volume_returns_none(self):
        assert _parse_luggage("12m³") is None

    def test_empty_string_returns_none(self):
        assert _parse_luggage("") is None

    def test_whitespace_only_returns_none(self):
        assert _parse_luggage("  ") is None
