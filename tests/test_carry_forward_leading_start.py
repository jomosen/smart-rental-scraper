"""Unit tests for carry_forward_leading_start (stable season start across scrapes)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.scraper.application.smart_scraping.smart_orchestrator import (
    carry_forward_leading_start,
)
from src.shared.domain.models.season import HomogeneousZone


def _zone(start, end, price, rep=None):
    return HomogeneousZone(
        start_date=start,
        end_date=end,
        reference_price=Decimal(str(price)),
        car_group="",
        representative_date=rep or start,
    )


THRESHOLD = 0.05


def test_inherits_old_start_when_price_continuous():
    new = [_zone(date(2026, 6, 24), date(2026, 6, 30), 30.20)]
    out = carry_forward_leading_start(new, date(2026, 6, 20), Decimal("30.00"), THRESHOLD)
    assert out[0].start_date == date(2026, 6, 20)  # carried forward
    assert out[0].end_date == date(2026, 6, 30)    # untouched
    assert out[0].reference_price == Decimal("30.20")


def test_no_carry_when_price_changed_beyond_threshold():
    new = [_zone(date(2026, 6, 24), date(2026, 6, 30), 40.00)]
    out = carry_forward_leading_start(new, date(2026, 6, 20), Decimal("30.00"), THRESHOLD)
    assert out[0].start_date == date(2026, 6, 24)  # unchanged (different season)


def test_no_carry_when_old_start_not_earlier():
    new = [_zone(date(2026, 6, 24), date(2026, 6, 30), 30.00)]
    out = carry_forward_leading_start(new, date(2026, 6, 24), Decimal("30.00"), THRESHOLD)
    assert out[0].start_date == date(2026, 6, 24)


def test_no_op_when_no_prior_zone():
    new = [_zone(date(2026, 6, 24), date(2026, 6, 30), 30.00)]
    assert carry_forward_leading_start(new, None, None, THRESHOLD)[0].start_date == date(2026, 6, 24)


def test_empty_new_zones():
    assert carry_forward_leading_start([], date(2026, 6, 20), Decimal("30"), THRESHOLD) == []


def test_only_leading_zone_adjusted():
    new = [
        _zone(date(2026, 6, 24), date(2026, 6, 30), 30.00),
        _zone(date(2026, 7, 1), date(2026, 7, 15), 45.00),
    ]
    out = carry_forward_leading_start(new, date(2026, 6, 20), Decimal("30.00"), THRESHOLD)
    assert out[0].start_date == date(2026, 6, 20)   # leading carried
    assert out[1].start_date == date(2026, 7, 1)    # rest untouched
    assert out[1].reference_price == Decimal("45.00")
