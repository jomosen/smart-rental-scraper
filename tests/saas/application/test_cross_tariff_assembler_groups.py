"""Unit tests for provider-group identity in the cross-tariff assembler.

No database, no I/O.

A ProviderRow is one *vehicle group of a provider*, not one provider: a provider
that segments a single ACRISS code into two price tiers yields two rows. These
tests pin the identity carried on each row (`external_code`, falling back to
`attributes_hash`), which is what a tenant→group mapping persists.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd

from src.saas.application.pricing.cross_tariff_assembler import assemble_cross_tariff

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


def _row(
    provider: str,
    ext_code: str | None,
    total: int,
    *,
    attributes_hash: str | None = None,
    models: str = "Ford Focus",
    acriss: str = "EDMR",
    dur: int = 3,
) -> dict:
    return {
        "acriss_code": acriss,
        "acriss_display_name": "Economy Manual",
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


def _assemble(*rows: dict, providers: list[str] | None = None):
    providers = providers or ["centauro"]
    return assemble_cross_tariff(
        df=pd.DataFrame(list(rows), columns=COLS),
        providers=providers,
        master=providers[0],
        base="min",
        round_mode="0",
        global_rule=DEFAULT_RULE,
        category_rules={},
        durations=[3],
        examples={},
        zone_index=0,
        today=JAN15,
    )


def _rows_of(payload):
    assert len(payload.categories) == 1
    return payload.categories[0].providers


class TestGroupIdentityOnProviderRow:
    def test_external_code_is_exposed(self):
        payload = _assemble(_row("centauro", "Grupo A", 90))
        (row,) = _rows_of(payload)
        assert row.external_code == "Grupo A"
        assert row.attributes_hash is None

    def test_two_groups_same_acriss_stay_separate(self):
        """The real shape: one provider, one ACRISS code, two price tiers."""
        payload = _assemble(
            _row("centauro", "Grupo A", 90, models="FIAT 500"),
            _row("centauro", "Grupo A1", 120, models="KIA PICANTO"),
        )
        rows = _rows_of(payload)
        assert len(rows) == 2
        assert {r.external_code for r in rows} == {"Grupo A", "Grupo A1"}
        # Each row keeps its own price — no collapsing to the provider minimum.
        by_code = {r.external_code: r.cells[0].total for r in rows}
        assert by_code == {"Grupo A": Decimal("90"), "Grupo A1": Decimal("120")}

    def test_codeless_groups_fall_back_to_attributes_hash(self):
        """A provider exposing no group codes must not collapse into one row.

        Deduplicating on external_code alone would merge these, because pandas
        treats NaN as equal to NaN.
        """
        payload = _assemble(
            _row("centauro", None, 90, attributes_hash="a1b2c3d4e5f60718"),
            _row("centauro", None, 120, attributes_hash="0718f6e5d4c3b2a1"),
        )
        rows = _rows_of(payload)
        assert len(rows) == 2
        assert all(r.external_code is None for r in rows)
        assert {r.attributes_hash for r in rows} == {
            "a1b2c3d4e5f60718",
            "0718f6e5d4c3b2a1",
        }

    def test_frame_without_attributes_hash_column_still_assembles(self):
        """Callers holding a frame built before the column existed keep working."""
        legacy_cols = [c for c in COLS if c != "attributes_hash"]
        row = _row("centauro", "Grupo A", 90)
        del row["attributes_hash"]
        payload = assemble_cross_tariff(
            df=pd.DataFrame([row], columns=legacy_cols),
            providers=["centauro"], master="centauro", base="min", round_mode="0",
            global_rule=DEFAULT_RULE, category_rules={}, durations=[3],
            examples={}, zone_index=0, today=JAN15,
        )
        (assembled,) = _rows_of(payload)
        assert assembled.external_code == "Grupo A"
        assert assembled.attributes_hash is None

    def test_groups_of_different_providers_are_independent(self):
        payload = _assemble(
            _row("centauro", "Grupo A", 90),
            _row("solcar", "Grupo B", 85),
            providers=["centauro", "solcar"],
        )
        rows = _rows_of(payload)
        assert {(r.provider_key, r.external_code) for r in rows} == {
            ("centauro", "Grupo A"),
            ("solcar", "Grupo B"),
        }
