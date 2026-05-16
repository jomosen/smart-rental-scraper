"""Integration tests for scripts/seed_taxonomy.py.

All tests use super_db_session (BYPASSRLS, rolled back after each test).
seed_taxonomy() does not commit — the session fixture's rollback is the
cleanup mechanism, so no explicit finally/cleanup blocks are needed
except where pre-committed data is set up in independent sessions.

Each test uses unique taxonomy codes (suffixed with the test number) to
avoid UNIQUE constraint violations within the same session scope.
"""
from __future__ import annotations

import logging

import pytest
from sqlalchemy import func, select

from scripts.seed_taxonomy import SeedError, seed_taxonomy
from src.saas.infrastructure.persistence.models.catalog import (
    CanonicalVehicleType,
    Provider,
    ProviderLocation,
    ProviderRate,
    ProviderVehicleCategory,
)


# ---------------------------------------------------------------------------
# YAML builder helpers
# ---------------------------------------------------------------------------

def _yaml(categories: list[dict], version: int = 1, initial_classification=None) -> dict:
    data: dict = {"taxonomy_version": version, "categories": categories}
    if initial_classification is not None:
        data["initial_classification"] = initial_classification
    return data


def _cat(code: str, name: str | None = None, description: str = "Test description") -> dict:
    return {"code": code, "name": name or code, "description": description}


def _setup_provider_with_pvc(
    session,
    provider_code: str,
    external_code: str,
    canonical_type_id=None,
) -> tuple[Provider, ProviderVehicleCategory]:
    """Create Provider → Location → Rate → PVC in the given session and flush."""
    p = Provider(
        code=provider_code,
        display_name=f"Provider {provider_code}",
        scraper_key=provider_code,
        default_currency="EUR",
        status="active",
    )
    session.add(p)
    session.flush()

    loc = ProviderLocation(
        provider_id=p.id,
        location_code="TST",
        location_name="Test Location",
        active=True,
    )
    rate = ProviderRate(
        provider_id=p.id,
        rate_code="test_rate",
        rate_name="Test Rate",
        active=True,
    )
    session.add_all([loc, rate])
    session.flush()

    pvc = ProviderVehicleCategory(
        provider_id=p.id,
        provider_location_id=loc.id,
        provider_rate_id=rate.id,
        external_code=external_code,
        external_name=external_code,
        example_models="",
        canonical_type_id=canonical_type_id,
        active=True,
    )
    session.add(pvc)
    session.flush()
    return p, pvc


# ---------------------------------------------------------------------------
# Test 1 — inserts new canonical types on empty DB
# ---------------------------------------------------------------------------

class TestSeedInsertsNewTypes:
    def test_inserts_new_canonical_types_on_empty_db(self, super_db_session):
        yaml_data = _yaml([
            _cat("SEED_T01_A", "Type A"),
            _cat("SEED_T01_B", "Type B"),
        ])
        pre_existing = super_db_session.scalar(
            select(func.count()).select_from(CanonicalVehicleType)
            .where(CanonicalVehicleType.active == True)  # noqa: E712
        )
        stats = seed_taxonomy(super_db_session, yaml_data)

        assert stats.inserted == 2
        assert stats.updated == 0
        assert stats.deprecated == pre_existing  # committed real-taxonomy types not in test YAML

        ct_a = super_db_session.scalar(
            select(CanonicalVehicleType).where(CanonicalVehicleType.code == "SEED_T01_A")
        )
        ct_b = super_db_session.scalar(
            select(CanonicalVehicleType).where(CanonicalVehicleType.code == "SEED_T01_B")
        )
        assert ct_a is not None
        assert ct_a.name == "Type A"
        assert ct_a.taxonomy_version == 1
        assert ct_a.active is True
        assert ct_b is not None
        assert ct_b.name == "Type B"


# ---------------------------------------------------------------------------
# Test 2 — idempotent: second run produces no changes
# ---------------------------------------------------------------------------

class TestSeedIdempotent:
    def test_second_run_produces_no_changes(self, super_db_session):
        yaml_data = _yaml([_cat("SEED_T02_A", "Type A")])

        stats1 = seed_taxonomy(super_db_session, yaml_data)
        assert stats1.inserted == 1

        ct = super_db_session.scalar(
            select(CanonicalVehicleType).where(CanonicalVehicleType.code == "SEED_T02_A")
        )
        created_at = ct.created_at

        stats2 = seed_taxonomy(super_db_session, yaml_data)
        assert stats2.inserted == 0
        assert stats2.updated == 0
        assert stats2.deprecated == 0

        super_db_session.refresh(ct)
        assert ct.created_at == created_at


# ---------------------------------------------------------------------------
# Test 3 — updates changed description
# ---------------------------------------------------------------------------

class TestSeedUpdatesDescription:
    def test_updates_changed_description(self, super_db_session):
        yaml_v1 = _yaml([_cat("SEED_T03_A", description="Old description")], version=1)
        seed_taxonomy(super_db_session, yaml_v1)

        yaml_v2 = _yaml([_cat("SEED_T03_A", description="New description")], version=2)
        stats = seed_taxonomy(super_db_session, yaml_v2)

        assert stats.updated == 1
        assert stats.inserted == 0

        ct = super_db_session.scalar(
            select(CanonicalVehicleType).where(CanonicalVehicleType.code == "SEED_T03_A")
        )
        assert ct.description == "New description"
        assert ct.taxonomy_version == 2


# ---------------------------------------------------------------------------
# Test 4 — deprecates categories missing from YAML
# ---------------------------------------------------------------------------

class TestSeedDeprecatesMissing:
    def test_deprecates_missing_categories(self, super_db_session):
        pre_existing = super_db_session.scalar(
            select(func.count()).select_from(CanonicalVehicleType)
            .where(CanonicalVehicleType.active == True)  # noqa: E712
        )
        ct_extra = CanonicalVehicleType(
            code="SEED_T04_GONE",
            name="Will be deprecated",
            description="This category is going away",
            taxonomy_version=1,
            active=True,
        )
        super_db_session.add(ct_extra)
        super_db_session.flush()

        yaml_data = _yaml([_cat("SEED_T04_KEEP")])
        stats = seed_taxonomy(super_db_session, yaml_data)

        assert stats.deprecated == pre_existing + 1  # pre-existing real-taxonomy types + SEED_T04_GONE

        super_db_session.refresh(ct_extra)
        assert ct_extra.active is False
        assert ct_extra.deprecated_at is not None

        # Row is not deleted — FK references remain valid
        still_there = super_db_session.scalar(
            select(CanonicalVehicleType).where(CanonicalVehicleType.code == "SEED_T04_GONE")
        )
        assert still_there is not None


# ---------------------------------------------------------------------------
# Test 5 — applies initial_classification to unclassified PVCs
# ---------------------------------------------------------------------------

class TestSeedAppliesClassification:
    def test_applies_initial_classification_to_unclassified_pvcs(self, super_db_session):
        _, pvc = _setup_provider_with_pvc(super_db_session, "seed_t05_prov", "A")

        yaml_data = _yaml(
            [_cat("SEED_T05_ECO", "Economy Manual")],
            initial_classification=[{
                "provider_code": "seed_t05_prov",
                "external_code": "A",
                "canonical_type_code": "SEED_T05_ECO",
                "confidence": 0.95,
            }],
        )
        stats = seed_taxonomy(super_db_session, yaml_data)

        assert stats.classified == 1

        super_db_session.refresh(pvc)
        ct = super_db_session.scalar(
            select(CanonicalVehicleType).where(CanonicalVehicleType.code == "SEED_T05_ECO")
        )
        assert pvc.canonical_type_id == ct.id
        assert pvc.classification_confidence == 0.95
        assert pvc.classification_taxonomy_version == 1


# ---------------------------------------------------------------------------
# Test 6 — does not overwrite existing classification
# ---------------------------------------------------------------------------

class TestSeedDoesNotOverwriteClassification:
    def test_does_not_overwrite_existing_classification(self, super_db_session):
        ct_existing = CanonicalVehicleType(
            code="SEED_T06_ORIG",
            name="Original type",
            description="Already classified into this",
            taxonomy_version=1,
            active=True,
        )
        super_db_session.add(ct_existing)
        super_db_session.flush()

        _, pvc = _setup_provider_with_pvc(
            super_db_session,
            "seed_t06_prov",
            "A",
            canonical_type_id=ct_existing.id,
        )
        original_ct_id = pvc.canonical_type_id

        yaml_data = _yaml(
            [_cat("SEED_T06_ORIG"), _cat("SEED_T06_OTHER", "Other type")],
            initial_classification=[{
                "provider_code": "seed_t06_prov",
                "external_code": "A",
                "canonical_type_code": "SEED_T06_OTHER",
                "confidence": 0.99,
            }],
        )
        stats = seed_taxonomy(super_db_session, yaml_data)

        assert stats.classified == 0

        super_db_session.refresh(pvc)
        assert pvc.canonical_type_id == original_ct_id


# ---------------------------------------------------------------------------
# Test 7 — skips missing provider with warning, continues
# ---------------------------------------------------------------------------

class TestSeedSkipsMissingProvider:
    def test_skips_missing_provider_with_warning(self, super_db_session, caplog):
        yaml_data = _yaml(
            [_cat("SEED_T07_ECO")],
            initial_classification=[{
                "provider_code": "nonexistent_t07_prov",
                "external_code": "A",
                "canonical_type_code": "SEED_T07_ECO",
                "confidence": 0.90,
            }],
        )
        with caplog.at_level(logging.WARNING, logger="scripts.seed_taxonomy"):
            stats = seed_taxonomy(super_db_session, yaml_data)

        assert stats.classified == 0
        assert "nonexistent_t07_prov" in caplog.text

        # Category was still inserted (step 1 ran successfully)
        ct = super_db_session.scalar(
            select(CanonicalVehicleType).where(CanonicalVehicleType.code == "SEED_T07_ECO")
        )
        assert ct is not None


# ---------------------------------------------------------------------------
# Test 8 — dry-run makes no changes
# ---------------------------------------------------------------------------

class TestSeedDryRun:
    def test_dry_run_makes_no_changes(self, super_db_session):
        yaml_data = _yaml([_cat("SEED_T08_DRY", "Dry type")])

        stats = seed_taxonomy(super_db_session, yaml_data, dry_run=True)

        assert stats.inserted == 1  # counted as "would insert"

        ct = super_db_session.scalar(
            select(CanonicalVehicleType).where(CanonicalVehicleType.code == "SEED_T08_DRY")
        )
        assert ct is None  # nothing flushed to session


# ---------------------------------------------------------------------------
# Test 9 — fails on unknown canonical_type in initial_classification
# ---------------------------------------------------------------------------

class TestSeedFailsOnInvalidYaml:
    def test_fails_on_unknown_canonical_in_classification(self, super_db_session):
        yaml_data = _yaml(
            [_cat("SEED_T09_KNOWN")],
            initial_classification=[{
                "provider_code": "seed_t09_prov",
                "external_code": "A",
                "canonical_type_code": "SEED_T09_NONEXISTENT",
                "confidence": 0.90,
            }],
        )
        with pytest.raises(SeedError, match="unknown canonical_type"):
            seed_taxonomy(super_db_session, yaml_data)

        # Validation fires before any DB work — no CT should have been inserted
        ct = super_db_session.scalar(
            select(CanonicalVehicleType).where(CanonicalVehicleType.code == "SEED_T09_KNOWN")
        )
        assert ct is None


# ---------------------------------------------------------------------------
# Test 10 — handles YAML without initial_classification block
# ---------------------------------------------------------------------------

class TestSeedEdgeCases:
    def test_handles_missing_initial_classification_block(self, super_db_session):
        yaml_data = _yaml([_cat("SEED_T10_ECO")])  # no initial_classification key

        stats = seed_taxonomy(super_db_session, yaml_data)

        assert stats.inserted == 1
        assert stats.classified == 0

        ct = super_db_session.scalar(
            select(CanonicalVehicleType).where(CanonicalVehicleType.code == "SEED_T10_ECO")
        )
        assert ct is not None
        assert ct.active is True
