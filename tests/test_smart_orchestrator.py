"""Unit tests for SmartScraperOrchestrator's pure analysis helpers and truncation logic.

Only _select_guide_group, _count_dates_for_group, and _reassign_representatives
are exercised here — no DB, no browser, no async.  The integration test at the
end checks that a centauro-like probe pattern produces multiple zones end-to-end.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from types import SimpleNamespace

from src.scraper.application.smart_scraping.smart_orchestrator import (
    SmartScraperOrchestrator,
    _partition_for_classification,
)
from src.scraper.domain.models.season_internals import PricePoint
from src.saas.application.classification.dtos import ClassificationResult
from src.saas.infrastructure.persistence.repositories.provider_vehicle_category_repository import (
    attributes_hash,
)
from src.shared.domain.models.result import BookingResult, Car
from src.shared.domain.models.search import BookingSearch, Location
from src.shared.domain.models.season import HomogeneousZone


def _cr(code: str = "CDMR", conf: float = 0.95, pending: bool = False) -> ClassificationResult:
    return ClassificationResult(code[0], code[1], code[2], code[3], conf, pending, None)


class TestPartitionForClassification:
    def test_reuses_on_hash_match(self):
        cached = {"A": ("h1", _cr("MDMR"))}
        reused, to_classify = _partition_for_classification({"A": "h1"}, cached)
        assert to_classify == []
        assert reused["A"].acriss_category == "M"

    def test_classifies_on_changed_attributes(self):
        cached = {"A": ("h1", _cr())}
        reused, to_classify = _partition_for_classification({"A": "h2"}, cached)
        assert reused == {} and to_classify == ["A"]

    def test_classifies_new_group(self):
        reused, to_classify = _partition_for_classification({"B": "h"}, {})
        assert reused == {} and to_classify == ["B"]


class TestClassifyProbeCatalogReuse:
    def test_only_changed_group_hits_the_llm(self):
        orch = _make_orchestrator()
        car_a = SimpleNamespace(example_models="Fiat 500", seats=5, luggage=2, transmission="manual")
        car_b = SimpleNamespace(example_models="VW Golf", seats=5, luggage=3, transmission="manual")
        probe = {"A": car_a, "B": car_b}

        # A's cached hash matches its current attributes; B's is stale → reclassify B only.
        cached = {
            "A": (attributes_hash("Fiat 500", 5, 2), _cr("MDMR")),
            "B": ("stale-hash", _cr("CDMR")),
        }
        orch._load_cached_classifications = lambda: cached
        orch._classification_service.classify_provider_batch.return_value = [_cr("CDAR")]

        out = orch._classify_probe_catalog(probe, {}, {})

        svc = orch._classification_service.classify_provider_batch
        svc.assert_called_once()
        sent = svc.call_args.args[1]
        assert [v.external_code for v in sent] == ["B"]   # only the changed group
        assert out["A"].acriss_category == "M"            # reused MDMR, no LLM
        assert out["B"].acriss_category == "C"            # fresh CDAR from LLM

    def test_no_llm_call_when_everything_cached(self):
        orch = _make_orchestrator()
        car = SimpleNamespace(example_models="Fiat 500", seats=5, luggage=2, transmission="manual")
        orch._load_cached_classifications = lambda: {
            "A": (attributes_hash("Fiat 500", 5, 2), _cr("MDMR")),
        }
        out = orch._classify_probe_catalog({"A": car}, {}, {})
        orch._classification_service.classify_provider_batch.assert_not_called()
        assert out["A"].acriss_category == "M"


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


# ──────────────────────────────────────────────────────────────────────────────
# _truncate_trailing_empties
# ──────────────────────────────────────────────────────────────────────────────

_LOC = Location(canonical_id="ALC", display_name="Alicante Airport")


def _search(d: date) -> BookingSearch:
    dt = datetime(d.year, d.month, d.day, 10, 0)
    dropoff = d + timedelta(days=7)
    return BookingSearch(
        provider_name="victoria",
        pickup_location=_LOC,
        dropoff_location=_LOC,
        pickup_at=dt,
        dropoff_at=datetime(dropoff.year, dropoff.month, dropoff.day, 10, 0),
    )


def _searches(n: int, start: date = date(2026, 6, 1)) -> list:
    return [_search(start + timedelta(days=i * 7)) for i in range(n)]


def _empty() -> BookingResult:
    return BookingResult(provider_name="victoria", is_confirmed_empty=True)


def _ok() -> BookingResult:
    return BookingResult(provider_name="victoria")


class TestTruncateTrailingEmpties:
    orch = _make_orchestrator()

    def _run(self, results, searches=None):
        if searches is None:
            searches = _searches(len(results))
        return self.orch._truncate_trailing_empties(searches, results)

    def test_three_trailing_empties_truncated(self):
        results = [_ok(), _ok(), _empty(), _empty(), _empty()]
        s, r, cut = self._run(results)
        assert cut == date(2026, 6, 1) + timedelta(days=14)   # index 2
        assert len(s) == 2
        assert len(r) == 2

    def test_two_trailing_empties_not_truncated(self):
        results = [_ok(), _ok(), _empty(), _empty()]
        s, r, cut = self._run(results)
        assert cut is None
        assert len(r) == 4

    def test_no_empties_not_truncated(self):
        results = [_ok(), _ok(), _ok()]
        s, r, cut = self._run(results)
        assert cut is None
        assert len(r) == 3

    def test_empty_in_middle_then_data_not_truncated(self):
        """Empties followed by data must not trigger truncation (streak resets)."""
        results = [_ok(), _empty(), _empty(), _empty(), _ok()]
        s, r, cut = self._run(results)
        assert cut is None
        assert len(r) == 5

    def test_error_result_none_resets_streak(self):
        """A None result inside the trailing empty run breaks the streak → no cut."""
        # Walking backward: _empty, _empty → streak=2; None → BREAK. streak < 3.
        results = [_ok(), _empty(), None, _empty(), _empty()]
        s, r, cut = self._run(results)
        assert cut is None

    def test_all_empty_truncated_from_start(self):
        """All confirmed-empty: full truncation leaves empty lists."""
        results = [_empty(), _empty(), _empty(), _empty()]
        s, r, cut = self._run(results)
        assert cut == date(2026, 6, 1)
        assert len(s) == 0
        assert len(r) == 0

    def test_truncated_at_date_is_first_empty_in_streak(self):
        results = [_ok(), _ok(), _ok(), _empty(), _empty(), _empty()]
        searches = _searches(6)
        s, r, cut = self.orch._truncate_trailing_empties(searches, results)
        assert cut == searches[3].pickup_at.date()  # index 3

    def test_returns_are_independent_lists(self):
        """Returned lists must not alias the originals."""
        results = [_ok(), _ok()]
        searches = [_search(date(2026, 6, 1 + i * 7)) for i in range(2)]
        s_orig = list(searches)
        s, r, _ = self.orch._truncate_trailing_empties(searches, results)
        s.append(None)
        assert len(searches) == len(s_orig)  # original not mutated


# ──────────────────────────────────────────────────────────────────────────────
# _make_probe_stopper
# ──────────────────────────────────────────────────────────────────────────────

class TestMakeProbeStopperTests:
    def _stopper(self, threshold: int = 3):
        return _make_orchestrator()._make_probe_stopper(threshold)

    def test_three_consecutive_empties_fires(self):
        stop = self._stopper()
        assert stop(_empty()) is False  # streak 1
        assert stop(_empty()) is False  # streak 2
        assert stop(_empty()) is True   # streak 3 → fire

    def test_cars_reset_streak(self):
        stop = self._stopper()
        assert stop(_empty()) is False
        assert stop(_empty()) is False
        assert stop(_ok()) is False     # streak resets
        assert stop(_empty()) is False
        assert stop(_empty()) is False
        assert stop(_empty()) is True   # fresh streak of 3

    def test_none_resets_streak(self):
        stop = self._stopper()
        assert stop(_empty()) is False
        assert stop(_empty()) is False
        assert stop(None) is False      # error/None resets streak
        assert stop(_empty()) is False
        assert stop(_empty()) is False
        assert stop(_empty()) is True   # fresh streak of 3

    def test_two_consecutive_not_enough(self):
        stop = self._stopper()
        assert stop(_empty()) is False
        assert stop(_empty()) is False
        assert stop(_ok()) is False     # never fires with only 2 in a row

    def test_custom_threshold(self):
        stop = self._stopper(threshold=2)
        assert stop(_empty()) is False
        assert stop(_empty()) is True

    def test_non_empty_result_never_fires(self):
        stop = self._stopper()
        for _ in range(10):
            assert stop(_ok()) is False


# ──────────────────────────────────────────────────────────────────────────────
# _retry_errored_searches
# ──────────────────────────────────────────────────────────────────────────────

def _errored(msg: str = "All attempts failed") -> BookingResult:
    return BookingResult(provider_name="centauro", errors=[msg])


def _with_cars(group: str = "A") -> BookingResult:
    return BookingResult(
        provider_name="centauro",
        cars=[Car(model="Fiat 500", group=group, description="", example_models="")],
    )


def _confirmed_empty() -> BookingResult:
    return BookingResult(provider_name="centauro", is_confirmed_empty=True)


class TestRetryErroredSearches:
    async def test_recovers_errored_and_leaves_others(self):
        orch = _make_orchestrator()
        requests = ["r0", "r1", "r2"]
        good, empty = _with_cars(), _confirmed_empty()
        results = [_errored(), good, empty]
        recovered = _with_cars()
        orch._run_session = AsyncMock(return_value=[recovered])

        out = await orch._retry_errored_searches(requests, results)

        # Only the errored search (index 0) is retried
        orch._run_session.assert_awaited_once_with(["r0"])
        assert out[0] is recovered   # replaced with the recovered result
        assert out[1] is good        # a good result is untouched
        assert out[2] is empty       # confirmed-empty is NOT retried

    async def test_no_retry_when_nothing_errored(self):
        orch = _make_orchestrator()
        results = [_with_cars(), _confirmed_empty()]
        orch._run_session = AsyncMock()

        out = await orch._retry_errored_searches(["r0", "r1"], results)

        orch._run_session.assert_not_awaited()
        assert out == results

    async def test_retry_that_still_fails_keeps_original(self):
        orch = _make_orchestrator()
        original = _errored()
        results = [original]
        orch._run_session = AsyncMock(return_value=[_errored("still broken")])

        out = await orch._retry_errored_searches(["r0"], results)

        assert out[0] is original    # not replaced by another errored result
