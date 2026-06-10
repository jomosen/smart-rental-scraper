"""Unit tests for the cross-tariff calculation pipeline.

No database, no I/O.  All functions are pure.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.saas.application.pricing.cross_tariff_calc import (
    RuleConfig,
    aggregate_cross_provider,
    apply_clamp,
    apply_rule,
    apply_rounding_directional,
    collapse_intra_provider,
    compute_cell,
    detect_anomalies,
)

D = Decimal


# ── Helper ───────────────────────────────────────────────────────────────────

def _rule(op="sub", val="1", mode="pct", floor="auto", ceiling="max"):
    return RuleConfig(op=op, val=D(val), mode=mode, floor_mode=floor, ceiling_mode=ceiling)


# ── 1. Intra-provider collapse ───────────────────────────────────────────────

class TestCollapseIntraProvider:
    def test_single_group_is_identity(self):
        assert collapse_intra_provider({"centauro": [D("100")]}) == {"centauro": D("100")}

    def test_multiple_groups_returns_min(self):
        assert collapse_intra_provider({"centauro": [D("100"), D("80"), D("120")]}) == {
            "centauro": D("80")
        }

    def test_multiple_providers(self):
        result = collapse_intra_provider({
            "centauro": [D("100"), D("90")],
            "solcar": [D("85")],
        })
        assert result == {"centauro": D("90"), "solcar": D("85")}

    def test_empty_provider_excluded(self):
        result = collapse_intra_provider({"centauro": [], "solcar": [D("70")]})
        assert "centauro" not in result
        assert result["solcar"] == D("70")


# ── 2. Anomaly detection ─────────────────────────────────────────────────────

class TestDetectAnomalies:
    def test_two_providers_never_anomaly(self):
        result = detect_anomalies({"a": D("100"), "b": D("2")})
        assert not any(result.values()), "with only 2 providers no outlier can be detected"

    def test_three_providers_low_outlier(self):
        # 2 € when others are ~100 € — ratio 0.02 < threshold 0.40
        result = detect_anomalies({"centauro": D("2"), "solcar": D("100"), "victoria": D("98")})
        assert result["centauro"] is True
        assert result["solcar"] is False
        assert result["victoria"] is False

    def test_three_providers_high_outlier(self):
        result = detect_anomalies({"centauro": D("400"), "solcar": D("100"), "victoria": D("102")})
        assert result["centauro"] is True

    def test_no_outlier_when_all_close(self):
        result = detect_anomalies({"a": D("100"), "b": D("105"), "c": D("98")})
        assert not any(result.values())

    def test_one_provider_never_anomaly(self):
        result = detect_anomalies({"a": D("50")})
        assert result["a"] is False

    def test_master_never_flagged(self):
        # Master is cheap vs the others but must never be dropped (it sets the calendar).
        result = detect_anomalies(
            {"centauro": D("2"), "solcar": D("100"), "victoria": D("98")},
            master="centauro",
        )
        assert result["centauro"] is False

    def test_inferred_witnesses_do_not_vote(self):
        # Centauro looks cheap only against inferred witnesses → not enough
        # direct witnesses (need ≥ 2), so no anomaly is flagged.
        result = detect_anomalies(
            {"centauro": D("62"), "solcar": D("191"), "victoria": D("139")},
            inferred={"solcar": True, "victoria": True},
        )
        assert not any(result.values())

    def test_one_direct_witness_is_insufficient(self):
        # Only one direct witness (solcar); victoria inferred → cannot judge.
        result = detect_anomalies(
            {"centauro": D("2"), "solcar": D("100"), "victoria": D("98")},
            inferred={"victoria": True},
        )
        assert not any(result.values())

    def test_cfar_regression_master_and_inferred(self):
        # Real CFAR 1d shape: master (centauro) cheap, both rivals inferred.
        # Either rule alone clears the false positive; together, definitely.
        collapsed = {"centauro": D("62.43"), "solcar": D("191.25"), "victoria": D("139.50")}
        result = detect_anomalies(
            collapsed,
            inferred={"solcar": True, "victoria": True},
            master="centauro",
        )
        assert result["centauro"] is False


# ── 3. Aggregation ───────────────────────────────────────────────────────────

class TestAggregateCrossProvider:
    _vals = [D("80"), D("100"), D("120"), D("140")]

    def test_min(self):
        assert aggregate_cross_provider(self._vals, "min") == D("80")

    def test_max(self):
        assert aggregate_cross_provider(self._vals, "max") == D("140")

    def test_avg(self):
        result = aggregate_cross_provider(self._vals, "avg")
        assert abs(float(result) - 110.0) < 0.01

    def test_med_even(self):
        result = aggregate_cross_provider(self._vals, "med")
        assert float(result) == 110.0

    def test_med_odd(self):
        result = aggregate_cross_provider([D("80"), D("100"), D("120")], "med")
        assert result == D("100")

    def test_empty_returns_none(self):
        assert aggregate_cross_provider([], "min") is None

    def test_unknown_base_raises(self):
        with pytest.raises(ValueError):
            aggregate_cross_provider([D("100")], "unknown")


# ── 4. Rule application ──────────────────────────────────────────────────────

class TestApplyRule:
    def test_sub_pct(self):
        result = apply_rule(D("100"), _rule(op="sub", val="5", mode="pct"))
        assert float(result) == pytest.approx(95.0)

    def test_add_pct(self):
        result = apply_rule(D("100"), _rule(op="add", val="10", mode="pct"))
        assert float(result) == pytest.approx(110.0)

    def test_sub_abs(self):
        result = apply_rule(D("100"), _rule(op="sub", val="8", mode="abs"))
        assert float(result) == pytest.approx(92.0)

    def test_add_abs(self):
        result = apply_rule(D("100"), _rule(op="add", val="15", mode="abs"))
        assert float(result) == pytest.approx(115.0)

    def test_zero_val_is_identity(self):
        result = apply_rule(D("100"), _rule(val="0", mode="pct"))
        assert float(result) == pytest.approx(100.0)


# ── 5. Clamp ─────────────────────────────────────────────────────────────────

class TestApplyClamp:
    def test_no_clamp_when_in_range(self):
        val, fired = apply_clamp(D("100"), D("80"), D("120"))
        assert val == D("100")
        assert fired is None

    def test_floor_triggered(self):
        val, fired = apply_clamp(D("50"), D("80"), D("120"))
        assert val == D("80")
        assert fired == "floor"

    def test_ceiling_triggered(self):
        val, fired = apply_clamp(D("150"), D("80"), D("120"))
        assert val == D("120")
        assert fired == "ceiling"

    def test_no_floor(self):
        val, fired = apply_clamp(D("20"), None, D("120"))
        assert val == D("20")
        assert fired is None

    def test_no_ceiling(self):
        val, fired = apply_clamp(D("200"), D("80"), None)
        assert val == D("200")
        assert fired is None


# ── 6. Rounding with directional guard ──────────────────────────────────────

class TestApplyRoundingDirectional:
    def test_no_rounding(self):
        val, flip = apply_rounding_directional(D("95.3"), D("100"), _rule(), "0")
        assert float(val) == pytest.approx(95.30, abs=0.01)
        assert flip is False

    def test_round_to_99_sub(self):
        val, flip = apply_rounding_directional(D("94.5"), D("100"), _rule(op="sub"), "0.99")
        assert str(val).endswith(".99")
        assert float(val) < 100.0

    def test_round_to_99_guard_prevents_crossing_base(self):
        # If rounding would bring val ≥ base (sub rule), guard must step down
        # base = 100, val = 99.5 → rounds to 99.99 without guard → crosses 100
        val, flip = apply_rounding_directional(D("99.5"), D("100"), _rule(op="sub"), "0.99")
        assert float(val) < 100.0

    def test_round_to_99_add_guard(self):
        # add rule: result must stay > base
        val, flip = apply_rounding_directional(D("100.4"), D("100"), _rule(op="add"), "0.99")
        assert float(val) > 100.0

    def test_round_to_90(self):
        val, flip = apply_rounding_directional(D("95.3"), D("100"), _rule(op="sub"), "0.90")
        assert str(val).endswith(".90")

    def test_round_to_half(self):
        val, flip = apply_rounding_directional(D("94.7"), D("100"), _rule(op="sub"), "0.50")
        assert float(val) % 0.5 == pytest.approx(0.0)

    def test_round_to_half_lands_on_50(self):
        # Reproducible bug: base 53.46, rule -1€ → after_rule 52.46 → 52.50 (not 52.00).
        val, flip = apply_rounding_directional(D("52.46"), D("53.46"), _rule(op="sub"), "0.50")
        assert val == D("52.50")
        assert flip is False

    def test_round_to_half_lands_on_00(self):
        # The other half of the fraction: 52.20 → nearest half is 52.00.
        val, flip = apply_rounding_directional(D("52.20"), D("53.46"), _rule(op="sub"), "0.50")
        assert val == D("52.00")

    def test_round_to_half_add_lands_on_50(self):
        # add rule, .xx that snaps up to .50
        val, flip = apply_rounding_directional(D("54.40"), D("53.46"), _rule(op="add"), "0.50")
        assert val == D("54.50")
        assert flip is False

    def test_round_to_half_guard_steps_by_half(self):
        # add rule, snap lands on/below base (53.20 → 53.00 ≤ 53.46) → guard nudges
        # up by exactly one step (0.5), not 1.
        val, flip = apply_rounding_directional(D("53.20"), D("53.46"), _rule(op="add"), "0.50")
        assert val == D("53.50")
        assert flip is True

    def test_round_to_integer(self):
        val, flip = apply_rounding_directional(D("94.6"), D("100"), _rule(op="sub"), "1")
        assert val == D("95.00")
        assert float(val) == float(int(float(val)))

    def test_round_to_integer_other_half(self):
        val, flip = apply_rounding_directional(D("94.4"), D("100"), _rule(op="sub"), "1")
        assert val == D("94.00")

    def test_round_to_99_other_half(self):
        # value already near a .99 boundary from below
        val, flip = apply_rounding_directional(D("95.6"), D("100"), _rule(op="sub"), "0.99")
        assert str(val).endswith(".99")
        assert val == D("95.99")

    def test_unknown_round_mode_raises(self):
        with pytest.raises(ValueError, match="Unknown round_mode"):
            apply_rounding_directional(D("95.3"), D("100"), _rule(op="sub"), "0.25")

    def test_zero_val_no_guard(self):
        val, flip = apply_rounding_directional(D("100.0"), D("100"), _rule(val="0"), "0.99")
        assert flip is False


# ── 7. Full compute_cell ──────────────────────────────────────────────────────

class TestComputeCell:
    def test_empty_when_no_providers(self):
        result = compute_cell(
            acriss_code="EDMR", duration=7,
            provider_groups={},
            stale_providers=set(), stale_days={},
            missing_providers={"centauro", "solcar"},
            rule=_rule(), base="min", round_mode="0",
        )
        assert result.rec_total is None
        assert result.flags.coverage == 0

    def test_single_provider_no_anomaly_check(self):
        result = compute_cell(
            acriss_code="EDMR", duration=7,
            provider_groups={"centauro": [D("266")]},
            stale_providers=set(), stale_days={},
            missing_providers=set(),
            rule=_rule(op="sub", val="5", mode="pct"),
            base="min", round_mode="0",
            total_providers=1,
        )
        assert result.rec_total is not None
        assert float(result.base_total) == pytest.approx(266.0)
        assert float(result.rec_total) < 266.0  # sub rule applied

    def test_anomaly_dropped_cell_still_computes(self):
        # centauro=2€ (anomaly), others=100€ → centauro dropped, base from others
        result = compute_cell(
            acriss_code="EDMR", duration=7,
            provider_groups={
                "centauro": [D("14")],   # 2€/day * 7
                "solcar":   [D("700")],  # 100€/day * 7
                "victoria": [D("686")],  # 98€/day * 7
            },
            stale_providers=set(), stale_days={},
            missing_providers=set(),
            rule=_rule(op="sub", val="0", mode="pct"),
            base="min", round_mode="0",
            total_providers=3,
        )
        assert result.flags.degraded is True
        assert result.flags.coverage == 2      # centauro dropped
        assert result.rec_total is not None
        assert float(result.rec_total) > 100   # centauro's 2€ total not included

    def test_intra_provider_collapse(self):
        # Two centauro groups: 100 and 80 → collapse to 80
        result = compute_cell(
            acriss_code="EDMR", duration=7,
            provider_groups={"centauro": [D("700"), D("560")]},
            stale_providers=set(), stale_days={},
            missing_providers=set(),
            rule=_rule(op="sub", val="0", mode="pct"),
            base="min", round_mode="0",
        )
        assert float(result.base_total) == pytest.approx(560.0)

    def test_per_day_is_derived(self):
        result = compute_cell(
            acriss_code="EDMR", duration=7,
            provider_groups={"centauro": [D("700")]},
            stale_providers=set(), stale_days={},
            missing_providers=set(),
            rule=_rule(op="sub", val="0", mode="pct"),
            base="min", round_mode="0",
        )
        assert float(result.rec_per_day) == pytest.approx(float(result.rec_total) / 7)
