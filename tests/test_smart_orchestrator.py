"""Unit tests for SmartScraperOrchestrator's pure analysis helpers.

Only _select_guide_group, _count_dates_for_group, and _reassign_representatives
are exercised here — no DB, no browser, no async.  The integration test at the
end checks that a centauro-like probe pattern produces multiple zones end-to-end.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.scraper.application.smart_scraping.smart_orchestrator import SmartScraperOrchestrator
from src.scraper.domain.models.season_internals import PricePoint
from src.shared.domain.models.season import HomogeneousZone


def _make_orchestrator() -> SmartScraperOrchestrator:
    return SmartScraperOrchestrator(
        factory=MagicMock(),
        probe=MagicMock(),
        analyzer=MagicMock(),
        plan_builder=MagicMock(),
        extractor=MagicMock(),
        session_factory=MagicMock(),
        provider_id=1,
        provider_location_id=1,
        provider_rate_id=1,
        classification_service=MagicMock(),
    )


def _pp(d: date, group: str, total: float = 100.0) -> PricePoint:
    return PricePoint(pickup_date=d, duration_days=7, total_price=Decimal(str(total)), car_group=group)


def _zone(start: date, end: date, rep: date, group: str = "A") -> HomogeneousZone:
    return HomogeneousZone(
        start_date=start,
        end_date=end,
        reference_price=Decimal("100"),
        car_group=group,
        representative_date=rep,
    )


# ──────────────────────────────────────────────────────────────────────────────
# _select_guide_group
# ──────────────────────────────────────────────────────────────────────────────

class TestSelectGuideGroup:
    def test_returns_group_with_most_distinct_dates(self):
        orch = _make_orchestrator()
        points = [
            _pp(date(2026, 6, 1),  "A"),
            _pp(date(2026, 6, 8),  "A"),
            _pp(date(2026, 6, 15), "A"),  # A → 3 distinct dates
            _pp(date(2026, 6, 1),  "C"),
            _pp(date(2026, 6, 8),  "C"),  # C → 2 distinct dates
        ]
        assert orch._select_guide_group(points) == "A"

    def test_tie_broken_by_total_point_count(self):
        orch = _make_orchestrator()
        # A and B both appear on 2 distinct dates, but A has 3 total points
        points = [
            _pp(date(2026, 6, 1),  "A"),
            _pp(date(2026, 6, 8),  "A"),
            _pp(date(2026, 6, 1),  "A", total=110.0),  # same date, extra point
            _pp(date(2026, 6, 1),  "B"),
            _pp(date(2026, 6, 8),  "B"),
        ]
        assert orch._select_guide_group(points) == "A"

    def test_returns_none_for_empty_input(self):
        orch = _make_orchestrator()
        assert orch._select_guide_group([]) is None

    def test_single_group_returned(self):
        orch = _make_orchestrator()
        points = [_pp(date(2026, 6, 1), "X"), _pp(date(2026, 6, 8), "X")]
        assert orch._select_guide_group(points) == "X"


# ──────────────────────────────────────────────────────────────────────────────
# _reassign_representatives
# ──────────────────────────────────────────────────────────────────────────────

class TestReassignRepresentatives:
    def test_picks_probe_date_with_most_groups_in_zone(self):
        orch = _make_orchestrator()
        zone = _zone(date(2026, 6, 1), date(2026, 6, 30), rep=date(2026, 6, 1))
        points = [
            _pp(date(2026, 6, 1),  "A"),              # 1 group on Jun 1
            _pp(date(2026, 6, 15), "A"),
            _pp(date(2026, 6, 15), "C"),
            _pp(date(2026, 6, 15), "D"),              # 3 groups on Jun 15
        ]
        result = orch._reassign_representatives([zone], points)
        assert result[0].representative_date == date(2026, 6, 15)

    def test_keeps_original_representative_when_no_probe_in_zone(self):
        orch = _make_orchestrator()
        zone = _zone(date(2026, 7, 1), date(2026, 7, 31), rep=date(2026, 7, 1))
        points = [
            _pp(date(2026, 6, 1),  "A"),
            _pp(date(2026, 6, 15), "B"),
        ]
        result = orch._reassign_representatives([zone], points)
        assert result[0].representative_date == date(2026, 7, 1)

    def test_tie_broken_by_later_probe_date(self):
        orch = _make_orchestrator()
        zone = _zone(date(2026, 6, 1), date(2026, 6, 30), rep=date(2026, 6, 1))
        # Jun 1 and Jun 15 both have 2 distinct groups → later date wins
        points = [
            _pp(date(2026, 6, 1),  "A"),
            _pp(date(2026, 6, 1),  "C"),
            _pp(date(2026, 6, 15), "A"),
            _pp(date(2026, 6, 15), "C"),
        ]
        result = orch._reassign_representatives([zone], points)
        assert result[0].representative_date == date(2026, 6, 15)

    def test_other_zone_fields_are_preserved(self):
        orch = _make_orchestrator()
        zone = _zone(date(2026, 6, 1), date(2026, 6, 30), rep=date(2026, 6, 1), group="Z")
        points = [_pp(date(2026, 6, 15), "A"), _pp(date(2026, 6, 15), "B")]
        result = orch._reassign_representatives([zone], points)
        z = result[0]
        assert z.start_date == date(2026, 6, 1)
        assert z.end_date == date(2026, 6, 30)
        assert z.car_group == "Z"
        assert z.reference_price == Decimal("100")
        assert z.representative_date == date(2026, 6, 15)

    def test_multiple_zones_each_reassigned_independently(self):
        orch = _make_orchestrator()
        z1 = _zone(date(2026, 6, 1),  date(2026, 6, 21), rep=date(2026, 6, 1))
        z2 = _zone(date(2026, 6, 22), date(2026, 6, 30), rep=date(2026, 6, 22))
        points = [
            _pp(date(2026, 6, 8),  "A"),
            _pp(date(2026, 6, 8),  "C"),   # Jun 8  → 2 groups, inside z1
            _pp(date(2026, 6, 22), "A"),
            _pp(date(2026, 6, 22), "C"),
            _pp(date(2026, 6, 22), "D"),   # Jun 22 → 3 groups, inside z2
        ]
        result = orch._reassign_representatives([z1, z2], points)
        assert result[0].representative_date == date(2026, 6, 8)
        assert result[1].representative_date == date(2026, 6, 22)


# ──────────────────────────────────────────────────────────────────────────────
# Integration: guide-group detection surfaces multiple zones
# ──────────────────────────────────────────────────────────────────────────────

class TestGuideGroupIntegration:
    def test_centauro_like_pattern_detects_multiple_zones(self):
        """With a stable guide group that has a clear price jump, zone detection
        must produce at least 2 zones — the behaviour that was broken before Fix A.
        """
        from src.scraper.application.smart_scraping.season_analyzer import SeasonAnalyzer

        orch = _make_orchestrator()

        # Group A: 4 probe dates, clear price jump at Jun 22 (~37% increase)
        # Group C: 2 probe dates only, stable — should NOT be chosen as guide
        points = [
            _pp(date(2026, 6, 8),  "A", total=210.0),  # 30 €/d
            _pp(date(2026, 6, 15), "A", total=215.0),  # 30.7 €/d
            _pp(date(2026, 6, 22), "A", total=295.0),  # 42.1 €/d  ← jump >5%
            _pp(date(2026, 6, 29), "A", total=300.0),  # 42.9 €/d
            _pp(date(2026, 6, 8),  "C", total=150.0),
            _pp(date(2026, 6, 15), "C", total=152.0),
        ]

        guide = orch._select_guide_group(points)
        assert guide == "A"  # 4 distinct dates > 2

        analyzer = SeasonAnalyzer(price_change_threshold=0.05)
        zones = analyzer.detect_zones(
            points, date(2026, 6, 1), date(2026, 9, 28), guide,
        )
        assert len(zones) >= 2
