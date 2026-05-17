"""Integration tests for scripts/seed_acriss_codes.py.

All tests use super_db_session (BYPASSRLS). seed_acriss_codes() does NOT
commit — the session fixture's rollback is the cleanup mechanism, so no
explicit finally/cleanup blocks are needed.

Tests use unique 4-char codes not present in the real acriss_codes.yaml to
avoid clashing with the seeded data already in the DB.
"""
from __future__ import annotations

import logging

import pytest
from sqlalchemy import select

from scripts.seed_acriss_codes import SeedError, seed_acriss_codes
from src.saas.infrastructure.persistence.models.catalog import AcrissCode


# ---------------------------------------------------------------------------
# YAML builder helpers
# ---------------------------------------------------------------------------

def _yaml(codes: list[dict]) -> dict:
    return {"acriss_codes": codes}


def _code(
    code: str,
    display_name: str | None = None,
    description: str = "Test description",
    criteria: list | None = None,
    examples: list | None = None,
) -> dict:
    return {
        "code": code,
        "display_name": display_name or code,
        "description": description,
        "criteria": criteria or [],
        "examples": examples or [],
    }


# ---------------------------------------------------------------------------
# Test 1 — inserts new ACRISS codes on first run
# ---------------------------------------------------------------------------

class TestSeedInsertsNewCodes:
    def test_inserts_new_acriss_codes(self, super_db_session):
        data = _yaml([
            _code("ZZMR", "Test Economy Manual"),
            _code("ZZAR", "Test Economy Auto"),
        ])
        stats = seed_acriss_codes(super_db_session, data)

        assert stats.inserted == 2
        assert stats.updated == 0

        row_a = super_db_session.scalar(
            select(AcrissCode).where(AcrissCode.code == "ZZMR")
        )
        assert row_a is not None
        assert row_a.display_name == "Test Economy Manual"
        assert row_a.active is True
        assert row_a.acriss_category == "Z"
        assert row_a.acriss_body_type == "Z"
        assert row_a.acriss_transmission == "M"
        assert row_a.acriss_fuel == "R"


# ---------------------------------------------------------------------------
# Test 2 — idempotent: second run produces no changes
# ---------------------------------------------------------------------------

class TestSeedIdempotent:
    def test_second_run_produces_no_changes(self, super_db_session):
        data = _yaml([_code("ZXMR", "Idempotent Test")])

        stats1 = seed_acriss_codes(super_db_session, data)
        assert stats1.inserted == 1

        row = super_db_session.scalar(
            select(AcrissCode).where(AcrissCode.code == "ZXMR")
        )
        first_updated_at = row.last_updated_at

        stats2 = seed_acriss_codes(super_db_session, data)
        assert stats2.inserted == 0
        assert stats2.updated == 0
        assert stats2.deactivated == 0

        super_db_session.refresh(row)
        assert row.last_updated_at == first_updated_at


# ---------------------------------------------------------------------------
# Test 3 — updates changed description or display_name
# ---------------------------------------------------------------------------

class TestSeedUpdatesChanged:
    def test_updates_changed_fields(self, super_db_session):
        data_v1 = _yaml([_code("ZYMR", description="Old description")])
        seed_acriss_codes(super_db_session, data_v1)

        data_v2 = _yaml([_code("ZYMR", description="New description")])
        stats = seed_acriss_codes(super_db_session, data_v2)

        assert stats.updated == 1
        assert stats.inserted == 0

        row = super_db_session.scalar(
            select(AcrissCode).where(AcrissCode.code == "ZYMR")
        )
        assert row.description == "New description"

    def test_updates_criteria_and_examples(self, super_db_session):
        data_v1 = _yaml([_code("ZVMR", criteria=["5 seats"], examples=["Old Car"])])
        seed_acriss_codes(super_db_session, data_v1)

        data_v2 = _yaml([_code("ZVMR", criteria=["7 seats"], examples=["New Car"])])
        stats = seed_acriss_codes(super_db_session, data_v2)

        assert stats.updated == 1
        row = super_db_session.scalar(
            select(AcrissCode).where(AcrissCode.code == "ZVMR")
        )
        assert row.criteria == ["7 seats"]
        assert row.examples == ["New Car"]


# ---------------------------------------------------------------------------
# Test 4 — deactivates codes absent from YAML
# ---------------------------------------------------------------------------

class TestSeedDeactivatesMissing:
    def test_deactivates_missing_codes(self, super_db_session):
        # Seed two codes, then re-seed with only one
        data_both = _yaml([_code("ZWMR"), _code("ZWAR")])
        seed_acriss_codes(super_db_session, data_both)

        row_gone = super_db_session.scalar(
            select(AcrissCode).where(AcrissCode.code == "ZWAR")
        )
        assert row_gone is not None and row_gone.active is True

        data_one = _yaml([_code("ZWMR")])
        stats = seed_acriss_codes(super_db_session, data_one)

        assert stats.deactivated >= 1
        super_db_session.refresh(row_gone)
        assert row_gone.active is False

    def test_deactivated_row_is_not_deleted(self, super_db_session):
        data = _yaml([_code("ZUMR")])
        seed_acriss_codes(super_db_session, data)

        seed_acriss_codes(super_db_session, _yaml([]))  # remove all

        still_there = super_db_session.scalar(
            select(AcrissCode).where(AcrissCode.code == "ZUMR")
        )
        assert still_there is not None  # soft-deleted, not gone


# ---------------------------------------------------------------------------
# Test 5 — re-activates a previously deactivated code
# ---------------------------------------------------------------------------

class TestSeedReactivates:
    def test_reactivates_deactivated_code(self, super_db_session):
        data = _yaml([_code("ZTMR")])
        seed_acriss_codes(super_db_session, data)
        seed_acriss_codes(super_db_session, _yaml([]))  # deactivate

        row = super_db_session.scalar(
            select(AcrissCode).where(AcrissCode.code == "ZTMR")
        )
        assert row.active is False

        seed_acriss_codes(super_db_session, data)  # re-seed
        super_db_session.refresh(row)
        assert row.active is True


# ---------------------------------------------------------------------------
# Test 6 — dry-run makes no DB changes
# ---------------------------------------------------------------------------

class TestSeedDryRun:
    def test_dry_run_makes_no_changes(self, super_db_session):
        data = _yaml([_code("ZSMR", "Dry Run Test")])

        stats = seed_acriss_codes(super_db_session, data, dry_run=True)

        assert stats.inserted == 1  # counted as "would insert"

        row = super_db_session.scalar(
            select(AcrissCode).where(AcrissCode.code == "ZSMR")
        )
        assert row is None  # nothing written to session


# ---------------------------------------------------------------------------
# Test 7 — validation rejects invalid YAML
# ---------------------------------------------------------------------------

class TestSeedValidation:
    def test_rejects_missing_acriss_codes_key(self):
        with pytest.raises(SeedError, match="acriss_codes"):
            seed_acriss_codes(None, {"wrong_key": []})

    def test_rejects_non_4char_code(self, super_db_session):
        data = _yaml([_code("ZZZ", "Three chars")])  # wrong length
        with pytest.raises(SeedError, match="4 characters"):
            seed_acriss_codes(super_db_session, data)

    def test_rejects_duplicate_codes(self, super_db_session):
        data = _yaml([_code("ZRMR"), _code("ZRMR")])
        with pytest.raises(SeedError, match="Duplicate"):
            seed_acriss_codes(super_db_session, data)

    def test_rejects_non_list_criteria(self, super_db_session):
        data = {"acriss_codes": [{
            "code": "ZRMR",
            "display_name": "Test",
            "description": "Test",
            "criteria": "not a list",
        }]}
        with pytest.raises(SeedError, match="criteria"):
            seed_acriss_codes(super_db_session, data)


# ---------------------------------------------------------------------------
# Test 8 — seeding real acriss_codes.yaml produces 26 codes
# ---------------------------------------------------------------------------

class TestSeedRealAcrissYaml:
    def test_real_yaml_produces_26_active_codes(self, super_db_session):
        import yaml
        from pathlib import Path

        yaml_path = Path(__file__).resolve().parents[3] / "acriss_codes.yaml"
        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        stats = seed_acriss_codes(super_db_session, data)

        # All 26 already seeded by migration — should be idempotent (0 changes)
        assert stats.inserted == 0
        assert stats.updated == 0
        assert stats.deactivated == 0
