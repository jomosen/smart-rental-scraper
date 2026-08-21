"""Unit tests for the un-collapsed per-group breakdown on ExportRow.

No database, no I/O.

`provider_prices` reduces a provider's groups to their minimum and
`provider_models` concatenates all their models, so the two can describe
different groups. `provider_groups` keeps them together: one entry per group,
with the identity to reference it and its own price, cheapest first.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd

from src.saas.application.pricing.export_service import PricingExportService

JAN1 = date(2025, 1, 1)
JAN31 = date(2025, 1, 31)
JAN15 = date(2025, 1, 15)

DEFAULT_RULE = {"op": "sub", "val": 1.0, "mode": "pct", "floor": "auto", "ceiling": "max"}

COLS = [
    "acriss_code", "acriss_display_name", "provider_code", "external_code",
    "attributes_hash", "example_models", "transmission", "acriss_transmission",
    "pending_review", "start_date", "end_date", "representative_date",
    "duration_days", "price_per_day", "total_price",
]


def _row(provider, ext_code, total, models="Ford Focus", dur=3, attributes_hash=None):
    return {
        "acriss_code": "MDMR",
        "acriss_display_name": "Mini Manual",
        "provider_code": provider,
        "external_code": ext_code,
        "attributes_hash": attributes_hash,
        "example_models": models,
        "transmission": "M",
        "acriss_transmission": "M",
        "pending_review": False,
        "start_date": JAN1,
        "end_date": JAN31,
        "representative_date": JAN15,
        "duration_days": dur,
        "price_per_day": Decimal(str(total)) / dur,
        "total_price": Decimal(str(total)),
    }


def _build(*rows, providers=None):
    providers = providers or ["centauro"]
    return PricingExportService().build_rows(
        df=pd.DataFrame(list(rows), columns=COLS),
        providers=providers,
        master=providers[0],
        base="min",
        round_mode="0",
        global_rule=DEFAULT_RULE,
        category_rules={},
        durations=[3],
        examples={},
        muted_categories=[],
    )


class TestProviderGroupsBreakdown:
    def test_each_group_keeps_its_own_price_and_identity(self):
        """The Fiat 500 / Kia Picanto case: two groups, one ACRISS code."""
        result = _build(
            _row("centauro", "Grupo A", 260, models="FIAT 500"),
            _row("centauro", "Grupo A1", 264, models="KIA PICANTO"),
        )
        (row,) = result.rows

        groups = row.provider_groups["centauro"]
        assert [(g.group_key, g.total) for g in groups] == [
            ("Grupo A", Decimal("260")),   # cheapest first
            ("Grupo A1", Decimal("264")),
        ]
        assert groups[0].models == "FIAT 500"
        assert groups[1].models == "KIA PICANTO"

    def test_base_group_is_flagged(self):
        result = _build(
            _row("centauro", "Grupo A", 260),
            _row("centauro", "Grupo A1", 264),
        )
        (row,) = result.rows

        flags = [g.is_base for g in row.provider_groups["centauro"]]
        assert flags == [True, False]  # base="min" → the cheaper group

    def test_collapsed_fields_are_unchanged(self):
        """The existing by_provider inputs keep their semantics — nothing breaks."""
        result = _build(
            _row("centauro", "Grupo A", 260, models="FIAT 500"),
            _row("centauro", "Grupo A1", 264, models="KIA PICANTO"),
        )
        (row,) = result.rows

        assert row.provider_prices["centauro"] == Decimal("260")
        assert row.provider_models["centauro"] == "FIAT 500 / KIA PICANTO"
        assert row.provider_external_codes["centauro"] == "Grupo A"

    def test_provider_without_data_has_empty_list(self):
        result = _build(
            _row("centauro", "Grupo A", 260),
            providers=["centauro", "solcar"],
        )
        (row,) = result.rows

        assert row.provider_groups["solcar"] == []

    def test_codeless_group_uses_attributes_hash_as_key(self):
        result = _build(
            _row("centauro", None, 260, attributes_hash="a1b2c3d4e5f60718"),
        )
        (row,) = result.rows

        (group,) = row.provider_groups["centauro"]
        assert group.group_key == "a1b2c3d4e5f60718"

    def test_groups_keyed_per_provider(self):
        result = _build(
            _row("centauro", "Grupo A", 260),
            _row("solcar", "Grupo B", 273),
            providers=["centauro", "solcar"],
        )
        (row,) = result.rows

        assert [g.group_key for g in row.provider_groups["centauro"]] == ["Grupo A"]
        assert [g.group_key for g in row.provider_groups["solcar"]] == ["Grupo B"]
