"""Integration tests for AcrissReviewQueueRepository (local Postgres).

Run with the DB up and migrations applied (docker compose up -d postgres &&
alembic upgrade head), like the rest of tests/saas."""
from __future__ import annotations

from src.saas.infrastructure.persistence.repositories import (
    AcrissReviewQueueRepository,
)


class TestAcrissReviewQueue:
    def test_upsert_creates_pending_row(self, super_db_session):
        repo = AcrissReviewQueueRepository(super_db_session)
        row = repo.upsert_sighting(
            normalized_model="chery tiggo 4",
            raw_model="Chery Tiggo 4",
            source="prov_a",
            suggested_category="I",
            suggested_type="G",
            suggested_acriss="IGMR",
            confidence=0.78,
            reason="semantic resolver",
        )
        assert row.status == "pending_review"
        assert row.sources_seen == ["prov_a"]

    def test_repeat_sighting_accumulates_sources(self, super_db_session):
        repo = AcrissReviewQueueRepository(super_db_session)
        repo.upsert_sighting("chery tiggo 4", "Chery Tiggo 4", "prov_a")
        row = repo.upsert_sighting(
            "chery tiggo 4", "Chery Tiggo 4", "prov_b",
            suggested_category="I", confidence=0.80,
        )
        assert set(row.sources_seen) == {"prov_a", "prov_b"}
        assert row.suggested_category == "I"

    def test_operator_verdict_is_not_overwritten(self, super_db_session):
        repo = AcrissReviewQueueRepository(super_db_session)
        repo.upsert_sighting(
            "chery tiggo 4", "Chery Tiggo 4", "prov_a", suggested_category="I"
        )
        assert repo.set_status("chery tiggo 4", "accepted") is True
        row = repo.upsert_sighting(
            "chery tiggo 4", "Chery Tiggo 4", "prov_c", suggested_category="C"
        )
        # sighting still recorded, but the accepted verdict and suggestion stay
        assert row.status == "accepted"
        assert row.suggested_category == "I"
        assert "prov_c" in row.sources_seen

    def test_list_pending_excludes_resolved(self, super_db_session):
        repo = AcrissReviewQueueRepository(super_db_session)
        repo.upsert_sighting("model a", "Model A", "p")
        repo.upsert_sighting("model b", "Model B", "p")
        repo.set_status("model a", "rejected")
        pending = {r.normalized_model for r in repo.list_pending()}
        assert "model b" in pending
        assert "model a" not in pending
