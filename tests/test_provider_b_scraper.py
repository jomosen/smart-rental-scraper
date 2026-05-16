"""Unit tests for provider_b_scraper parsing helpers."""
import pytest

from src.scraper.infrastructure.scrapers.provider_b_scraper import _parse_int


class TestParseInt:
    def test_plain_integer(self):
        assert _parse_int("5") == 5

    def test_single_digit(self):
        assert _parse_int("2") == 2

    def test_larger_value(self):
        assert _parse_int("9") == 9

    def test_whitespace_stripped(self):
        assert _parse_int("  3  ") == 3

    def test_empty_string_returns_none(self):
        assert _parse_int("") is None

    def test_non_numeric_returns_none(self):
        assert _parse_int("AUT") is None

    def test_float_string_returns_none(self):
        assert _parse_int("5.5") is None
