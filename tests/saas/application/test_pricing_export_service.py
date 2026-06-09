"""Unit tests for PricingExportService.

No database, no I/O.  The service takes a DataFrame and calls
assemble_cross_tariff N times — one per zone — over the same df.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from src.saas.application.pricing.export_service import (
    ExportResult,
    ExportRow,
    PricingExportService,
)

D = Decimal

# ── Shared dates ─────────────────────────────────────────────────────────────

JAN1  = date(2025, 1, 1);  JAN31 = date(2025, 1, 31);  JAN15 = date(2025, 1, 15)
FEB1  = date(2025, 2, 1);  FEB28 = date(2025, 2, 28);  FEB14 = date(2025, 2, 14)
MAR1  = date(2025, 3, 1);  MAR31 = date(2025, 3, 31);  MAR15 = date(2025, 3, 15)

DEFAULT_RULE = {
    "op": "sub", "val": 1.0, "mode": "pct",
    "floor": "auto", "ceiling": "max",
}

COLS = [
    "acriss_code", "acriss_display_name", "provider_code", "external_code",
    "example_models", "transmission", "acriss_transmission", "pending_review",
    "start_date", "end_date", "representative_date",
    "duration_days", "price_per_day", "total_price",
]


# ── DataFrame helpers ─────────────────────────────────────────────────────────

def _row(
    provider: str,
    ext_code: str,
    start: date,
    end: date,
    rep: date,
    dur: int,
    total: int | str,
    acriss: str = "EDMR",
) -> dict:
    t = D(str(total))
    return {
        "acriss_code": acriss,
        "acriss_display_name": "Economy Manual",
        "provider_code": provider,
        "external_code": ext_code,
        "example_models": "Ford Focus",
        "transmission": "M",
        "acriss_transmission": "M",
        "pending_review": False,
        "start_date": start,
        "end_date": end,
        "representative_date": rep,
        "duration_days": dur,
        "price_per_day": t / dur,
        "total_price": t,
    }


def _df(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


def _svc() -> PricingExportService:
    return PricingExportService()


def _build(df, providers=None, master="centauro", durations=None, **kw):
    providers = providers if providers is not None else ["centauro", "solcar"]
    durations = durations if durations is not None else [3]
    return _svc().build_rows(
        df=df,
        providers=providers,
        master=master,
        base=kw.pop("base", "min"),
        round_mode=kw.pop("round_mode", "0"),
        global_rule=kw.pop("global_rule", DEFAULT_RULE),
        category_rules=kw.pop("category_rules", {}),
        durations=durations,
        examples=kw.pop("examples", {}),
        **kw,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestAllZonesIncluded:
    def test_two_zones_produce_two_row_groups(self):
        """Output contains rows for every zone the master provider has."""
        df = _df(
            _row("centauro", "C1", JAN1, JAN31, JAN15, 3, 90),
            _row("centauro", "C1", FEB1, FEB28, FEB14, 3, 95),
            _row("solcar",   "S1", JAN1, JAN31, JAN15, 3, 85),
            _row("solcar",   "S1", FEB1, FEB28, FEB14, 3, 88),
        )
        result = _build(df)

        zone_indices = {r.zone_index for r in result.rows}
        assert zone_indices == {0, 1}
        assert len(result.rows) == 2   # 2 zones × 1 category × 1 duration

    def test_three_zones_produce_three_row_groups(self):
        df = _df(
            _row("centauro", "C1", JAN1, JAN31, JAN15, 3, 90),
            _row("centauro", "C1", FEB1, FEB28, FEB14, 3, 95),
            _row("centauro", "C1", MAR1, MAR31, MAR15, 3, 92),
        )
        result = _build(df, providers=["centauro"])

        assert {r.zone_index for r in result.rows} == {0, 1, 2}
        assert len(result.rows) == 3

    def test_zone_date_range_stamped_on_each_row(self):
        df = _df(
            _row("centauro", "C1", JAN1, JAN31, JAN15, 3, 90),
            _row("centauro", "C1", FEB1, FEB28, FEB14, 3, 95),
        )
        result = _build(df, providers=["centauro"])

        by_zone = {r.zone_index: r for r in result.rows}
        assert by_zone[0].zone_desde == JAN1
        assert by_zone[0].zone_hasta == JAN31
        assert by_zone[1].zone_desde == FEB1
        assert by_zone[1].zone_hasta == FEB28


class TestProviderPrices:
    def test_each_provider_gets_its_own_price(self):
        """Both providers show their individual prices, not just the base provider."""
        df = _df(
            _row("centauro", "C1", JAN1, JAN31, JAN15, 3, 90),
            _row("solcar",   "S1", JAN1, JAN31, JAN15, 3, 85),
        )
        result = _build(df)

        assert len(result.rows) == 1
        row = result.rows[0]
        assert row.provider_prices["centauro"] == D("90")
        assert row.provider_prices["solcar"]   == D("85")

    def test_missing_provider_yields_none(self):
        """A provider absent from a cell produces None in provider_prices."""
        df = _df(
            _row("centauro", "C1", JAN1, JAN31, JAN15, 3, 90),
            # solcar has no data
        )
        result = _build(df)

        assert len(result.rows) == 1
        assert result.rows[0].provider_prices["solcar"] is None

    def test_minimum_group_chosen_per_provider(self):
        """When a provider has multiple groups, the cheapest one is used."""
        df = _df(
            _row("centauro", "C1", JAN1, JAN31, JAN15, 3, 100),  # expensive group
            _row("centauro", "C2", JAN1, JAN31, JAN15, 3, 90),   # cheaper group
            _row("solcar",   "S1", JAN1, JAN31, JAN15, 3, 85),
        )
        result = _build(df)

        assert result.rows[0].provider_prices["centauro"] == D("90")

    def test_all_providers_have_price_regardless_of_base_mode(self):
        """With base='avg' no provider gets is_base=True; both columns still fill."""
        df = _df(
            _row("centauro", "C1", JAN1, JAN31, JAN15, 3, 90),
            _row("solcar",   "S1", JAN1, JAN31, JAN15, 3, 85),
        )
        result = _build(df, base="avg")

        row = result.rows[0]
        assert row.provider_prices["centauro"] is not None
        assert row.provider_prices["solcar"]   is not None


class TestRecommendedPrice:
    def test_recommended_total_and_per_day_populated(self):
        df = _df(
            _row("centauro", "C1", JAN1, JAN31, JAN15, 3, 90),
            _row("solcar",   "S1", JAN1, JAN31, JAN15, 3, 85),
        )
        result = _build(df)

        row = result.rows[0]
        # base="min" → base_total=85; rule sub 1% → 85*0.99 = 84.15
        assert row.recomendado_total is not None
        assert row.recomendado_per_day is not None
        assert row.duracion_dias == 3


class TestEmptyCellsSkipped:
    def test_empty_cell_not_in_output(self):
        """A duration with no data for any provider produces no ExportRow."""
        df = _df(
            # Only duration=3 exists; duration=7 will be empty
            _row("centauro", "C1", JAN1, JAN31, JAN15, 3, 90),
        )
        result = _build(df, providers=["centauro"], durations=[3, 7])

        durations_in_output = {r.duracion_dias for r in result.rows}
        assert 3 in durations_in_output
        assert 7 not in durations_in_output


class TestEmptyInputs:
    def test_empty_df_returns_no_rows(self):
        result = _build(pd.DataFrame(columns=COLS))
        assert result.rows == []
        assert result.providers == ["centauro", "solcar"]

    def test_empty_providers_list_returns_no_rows(self):
        df = _df(_row("centauro", "C1", JAN1, JAN31, JAN15, 3, 90))
        result = _build(df, providers=[])
        assert result.rows == []


class TestResultMetadata:
    def test_providers_and_durations_preserved_in_result(self):
        df = _df(_row("centauro", "C1", JAN1, JAN31, JAN15, 3, 90))
        result = _build(
            df,
            providers=["centauro", "solcar", "victoria"],
            durations=[3, 7, 14],
        )
        assert result.providers == ["centauro", "solcar", "victoria"]
        assert result.durations == [3, 7, 14]
