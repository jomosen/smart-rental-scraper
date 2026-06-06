"""Integration tests for AcrissCodeRepository.

Uses super_db_session (BYPASSRLS). The session fixture rolls back after each
test so no explicit cleanup is needed. Tests use codes not in the real
acriss_codes.yaml to avoid conflicts with the seeded data.
"""
from __future__ import annotations

from sqlalchemy import select

from src.saas.infrastructure.persistence.models.catalog import AcrissCode
from src.saas.infrastructure.persistence.repositories import AcrissCodeRepository


# ---------------------------------------------------------------------------
# Test 1 — upsert inserts a new row on first call
# ---------------------------------------------------------------------------

class TestUpsertInsertsNew:
    def test_inserts_new_row(self, super_db_session):
        repo = AcrissCodeRepository(super_db_session)
        row = repo.upsert(
            "ZZMR",
            display_name="Test Economy",
            description="A test entry",
            criteria=["5 seats", "manual"],
            examples=["Fiat Panda"],
        )

        assert row.code == "ZZMR"
        assert row.display_name == "Test Economy"
        assert row.description == "A test entry"
        assert row.criteria == ["5 seats", "manual"]
        assert row.examples == ["Fiat Panda"]
        assert row.active is True
        assert row.created_at is not None
        assert row.last_updated_at is not None

    def test_sets_acriss_position_attrs_from_code(self, super_db_session):
        repo = AcrissCodeRepository(super_db_session)
        row = repo.upsert("CGMR", "Crossover Manual", "desc", [], [])

        assert row.acriss_category == "C"
        assert row.acriss_body_type == "G"
        assert row.acriss_transmission == "M"
        assert row.acriss_fuel == "R"

    def test_raises_for_non_4char_code(self, super_db_session):
        repo = AcrissCodeRepository(super_db_session)
        import pytest
        with pytest.raises(ValueError, match="4 characters"):
            repo.upsert("ZZZ", "Three chars", "desc", [], [])


# ---------------------------------------------------------------------------
# Test 2 — upsert updates an existing row
# ---------------------------------------------------------------------------

class TestUpsertUpdatesExisting:
    def test_updates_display_name_and_description(self, super_db_session):
        repo = AcrissCodeRepository(super_db_session)
        repo.upsert("ZXMR", "Old Name", "Old desc", [], [])

        row = repo.upsert("ZXMR", "New Name", "New desc", ["criterion"], ["example"])

        assert row.display_name == "New Name"
        assert row.description == "New desc"
        assert row.criteria == ["criterion"]
        assert row.examples == ["example"]

    def test_does_not_create_duplicate_row(self, super_db_session):
        repo = AcrissCodeRepository(super_db_session)
        repo.upsert("ZYMR", "First", "desc", [], [])
        repo.upsert("ZYMR", "Second", "desc", [], [])

        rows = super_db_session.scalars(
            select(AcrissCode).where(AcrissCode.code == "ZYMR")
        ).all()
        assert len(rows) == 1

    def test_updates_last_updated_at(self, super_db_session):
        import time
        repo = AcrissCodeRepository(super_db_session)
        row = repo.upsert("ZVMR", "Name A", "desc", [], [])
        first_ts = row.last_updated_at

        time.sleep(0.01)
        repo.upsert("ZVMR", "Name B", "desc", [], [])
        super_db_session.refresh(row)

        assert row.last_updated_at >= first_ts


# ---------------------------------------------------------------------------
# Test 3 — get_by_code returns existing or None
# ---------------------------------------------------------------------------

class TestGetByCode:
    def test_returns_row_for_known_code(self, super_db_session):
        repo = AcrissCodeRepository(super_db_session)
        repo.upsert("ZWMR", "Known", "desc", [], [])

        row = repo.get_by_code("ZWMR")
        assert row is not None
        assert row.code == "ZWMR"

    def test_returns_none_for_unknown_code(self, super_db_session):
        repo = AcrissCodeRepository(super_db_session)
        row = repo.get_by_code("XXXX")
        assert row is None

    def test_returns_row_for_seeded_real_code(self, super_db_session):
        repo = AcrissCodeRepository(super_db_session)
        row = repo.get_by_code("EDMR")
        assert row is not None
        assert row.display_name  # has a non-empty display_name from real YAML seed


# ---------------------------------------------------------------------------
# Test 4 — list_active returns only active rows
# ---------------------------------------------------------------------------

class TestListActive:
    def test_returns_only_active_rows(self, super_db_session):
        repo = AcrissCodeRepository(super_db_session)
        repo.upsert("ZUHR", "Active", "desc", [], [])
        repo.upsert("ZUDR", "Inactive", "desc", [], [], active=False)

        active_codes = {r.code for r in repo.list_active()}
        assert "ZUHR" in active_codes
        assert "ZUDR" not in active_codes

    def test_returns_real_seeded_codes(self, super_db_session):
        repo = AcrissCodeRepository(super_db_session)
        active_codes = {r.code for r in repo.list_active()}
        assert "EDMR" in active_codes
        assert "EFMR" in active_codes
        assert "CGAR" not in active_codes  # body G dropped in the taxonomy redesign
        assert len(active_codes) >= 70  # ~72 codes after the redesign


# ---------------------------------------------------------------------------
# Test 5 — deactivate_missing marks absent codes inactive
# ---------------------------------------------------------------------------

class TestDeactivateMissing:
    def test_deactivates_codes_not_in_set(self, super_db_session):
        repo = AcrissCodeRepository(super_db_session)
        repo.upsert("ZTMR", "Keep", "desc", [], [])
        repo.upsert("ZTAR", "Remove", "desc", [], [])

        active_before = {r.code for r in repo.list_active()}
        count = repo.deactivate_missing(active_before - {"ZTAR"})

        assert count >= 1
        row_gone = repo.get_by_code("ZTAR")
        assert row_gone is not None
        assert row_gone.active is False

    def test_returns_zero_when_nothing_deactivated(self, super_db_session):
        repo = AcrissCodeRepository(super_db_session)
        repo.upsert("ZSMR", "Stays Active", "desc", [], [])

        all_active = {r.code for r in repo.list_active()}
        count = repo.deactivate_missing(all_active)

        assert count == 0

    def test_does_not_delete_deactivated_row(self, super_db_session):
        repo = AcrissCodeRepository(super_db_session)
        repo.upsert("ZRMR", "Will be deactivated", "desc", [], [])

        active_codes = {r.code for r in repo.list_active()} - {"ZRMR"}
        repo.deactivate_missing(active_codes)

        still_in_db = repo.get_by_code("ZRMR")
        assert still_in_db is not None
        assert still_in_db.active is False
