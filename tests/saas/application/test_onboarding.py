"""Tests for the tenant onboarding module.

Unit tests (TestLoadConfig): no DB required, use tmp_path fixture.
Integration tests: require local Postgres with migrations applied.
Each integration test that commits data cleans up in a finally block.
"""
from __future__ import annotations

import textwrap
import uuid
from datetime import datetime
from functools import partial
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, select

from src.saas.application.catalog_sync import CatalogSyncService
from src.saas.application.onboarding.config import ConfigError, OnboardingConfig, load_config
from src.saas.application.onboarding.rollback import rollback_tenant
from src.saas.application.onboarding.steps import (
    OnboardingError,
    step_activate_subscriptions,
    step_create_mappings,
    step_create_subscriptions,
    step_create_tenant,
    step_create_users_and_groups,
    step_discovery,
    step_validate_catalog,
)
from src.saas.infrastructure.persistence.engine import super_engine
from src.saas.infrastructure.persistence.models.catalog import (
    CanonicalVehicleType,
    HomogeneousZone,
    Provider,
    ProviderLocation,
    ProviderRate,
    ProviderVehicleCategory,
    PriceObservation,
    PriceObservationHeartbeat,
    ScrapeRun,
)
from src.saas.infrastructure.persistence.models.tenant import (
    Tenant,
    TenantSubscription,
    TenantVehicleGroup,
    TenantVehicleGroupMapping,
    User,
)
from src.saas.infrastructure.persistence.session import super_session
from src.scraper.application.smart_scraping.smart_orchestrator import SmartScraperOrchestrator

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

_PROVIDER_ENTRY = {
    "name": "Onboard Test Provider",
    "scraper": "onb_test_sc",
    "base_url": "https://onboard-test.example.com",
    "location_id": "ONB1",
    "location_name": "Onboard Test Location",
    "rate_name": "Test Rate",
    "enabled": True,
}

# Option B: mappings live inside subscriptions, not vehicle_groups.
# display_order is required.
_VALID_YAML = textwrap.dedent("""\
    tenant:
      name: Test Company
      currency: EUR
      owner_email: owner@test.com

    vehicle_groups:
      - code: compact
        name: Compact
        description: Small cars
        display_order: 1

    subscriptions:
      - provider_code: onb_test_sc
        location_code: ONB1
        rate_code: test_rate
        mappings:
          - client_group_code: compact
            external_codes:
              - ECMR
""")

_PERIOD_START = datetime(2026, 6, 1, 10, 0, 0)
_PERIOD_END = datetime(2026, 6, 10, 10, 0, 0)

_DUMMY_FACTORY = lambda *a, **kw: None  # noqa: E731 — never called when _run_session is mocked


def _setup_catalog(session) -> tuple[int, int, int]:
    service = CatalogSyncService(session)
    ids = service.sync_from_providers_json([_PROVIDER_ENTRY])
    return ids["Onboard Test Provider"]


def _setup_pvg(
    session,
    pid: int,
    lid: int,
    rid: int,
    ext_code: str = "ECMR",
    canonical_type_id=None,
) -> ProviderVehicleCategory:
    pvg = ProviderVehicleCategory(
        provider_id=pid,
        provider_location_id=lid,
        provider_rate_id=rid,
        external_code=ext_code,
        external_name=ext_code,
        example_models="",
        canonical_type_id=canonical_type_id,
    )
    session.add(pvg)
    session.flush()
    return pvg


def _cleanup(
    session,
    provider_id: int,
    tenant_id=None,
    canonical_type_ids=None,
) -> None:
    """Delete all test data in FK-safe order and commit."""
    session.rollback()
    if tenant_id is not None:
        rollback_tenant(tenant_id, session)
    session.execute(delete(TenantVehicleGroupMapping))
    session.execute(delete(PriceObservation).where(PriceObservation.provider_id == provider_id))
    session.execute(delete(PriceObservationHeartbeat).where(PriceObservationHeartbeat.provider_id == provider_id))
    session.execute(delete(HomogeneousZone).where(HomogeneousZone.provider_id == provider_id))
    session.execute(delete(ScrapeRun).where(ScrapeRun.provider_id == provider_id))
    session.execute(delete(ProviderVehicleCategory).where(ProviderVehicleCategory.provider_id == provider_id))
    session.execute(delete(ProviderRate).where(ProviderRate.provider_id == provider_id))
    session.execute(delete(ProviderLocation).where(ProviderLocation.provider_id == provider_id))
    session.execute(delete(Provider).where(Provider.id == provider_id))
    if canonical_type_ids:
        for ctid in canonical_type_ids:
            session.execute(delete(CanonicalVehicleType).where(CanonicalVehicleType.id == ctid))
    session.commit()


def _config_from_yaml(yaml_text: str, tmp_path) -> OnboardingConfig:
    p = tmp_path / "config.yaml"
    p.write_text(yaml_text, encoding="utf-8")
    return load_config(p)


# ---------------------------------------------------------------------------
# TestLoadConfig — unit tests, no DB required
# ---------------------------------------------------------------------------

class TestLoadConfig:
    def test_valid_yaml_parses_all_fields(self, tmp_path):
        config = _config_from_yaml(_VALID_YAML, tmp_path)
        assert config.tenant.name == "Test Company"
        assert config.tenant.currency == "EUR"
        assert config.tenant.owner_email == "owner@test.com"
        assert len(config.vehicle_groups) == 1
        grp = config.vehicle_groups[0]
        assert grp.code == "compact"
        assert grp.name == "Compact"
        assert grp.description == "Small cars"
        assert grp.display_order == 1
        assert len(config.subscriptions) == 1
        sub = config.subscriptions[0]
        assert sub.provider_code == "onb_test_sc"
        assert sub.location_code == "ONB1"
        assert sub.rate_code == "test_rate"
        assert len(sub.mappings) == 1
        assert sub.mappings[0].client_group_code == "compact"
        assert sub.mappings[0].external_codes == ["ECMR"]

    def test_validation_fails_on_currency_mismatch(self, tmp_path):
        # "US1" contains a digit — not 3 ASCII alpha
        yaml_text = _VALID_YAML.replace("currency: EUR", "currency: US1")
        with pytest.raises(ConfigError, match="currency"):
            _config_from_yaml(yaml_text, tmp_path)

    def test_validation_fails_on_unknown_client_group_code(self, tmp_path):
        yaml_text = _VALID_YAML.replace(
            "client_group_code: compact", "client_group_code: nonexistent"
        )
        with pytest.raises(ConfigError, match="not listed in vehicle_groups"):
            _config_from_yaml(yaml_text, tmp_path)

    def test_validation_fails_on_duplicate_client_group_code(self, tmp_path):
        yaml_text = textwrap.dedent("""\
            tenant:
              name: Test Company
              currency: EUR
              owner_email: owner@test.com

            vehicle_groups:
              - code: compact
                name: Compact
                display_order: 1
              - code: compact
                name: Compact Duplicate
                display_order: 2

            subscriptions:
              - provider_code: onb_test_sc
                location_code: ONB1
                rate_code: test_rate
                mappings:
                  - client_group_code: compact
                    external_codes: [ECMR]
        """)
        with pytest.raises(ConfigError, match="Duplicate vehicle_group code"):
            _config_from_yaml(yaml_text, tmp_path)

    def test_validation_fails_on_empty_provider_group_external_codes(self, tmp_path):
        yaml_text = textwrap.dedent("""\
            tenant:
              name: Test Company
              currency: EUR
              owner_email: owner@test.com

            vehicle_groups:
              - code: compact
                name: Compact
                display_order: 1

            subscriptions:
              - provider_code: onb_test_sc
                location_code: ONB1
                rate_code: test_rate
                mappings:
                  - client_group_code: compact
                    external_codes: []
        """)
        with pytest.raises(ConfigError, match="external_code"):
            _config_from_yaml(yaml_text, tmp_path)


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

def test_creates_tenant_and_subscribes_active_when_all_mappings_complete(
    super_db_session, tmp_path
):
    pid, lid, rid = _setup_catalog(super_db_session)
    ct_ecmr = CanonicalVehicleType(
        code="ECMR_ONB01", name="Economy Manual ONB01",
        description="Test canonical for ECMR", taxonomy_version=1,
    )
    super_db_session.add(ct_ecmr)
    super_db_session.flush()
    _setup_pvg(super_db_session, pid, lid, rid, "ECMR", canonical_type_id=ct_ecmr.id)
    super_db_session.commit()
    ct_ids = [ct_ecmr.id]
    tenant_id = None
    try:
        config = _config_from_yaml(_VALID_YAML, tmp_path)
        catalog_ids = {("onb_test_sc", "ONB1", "test_rate"): (pid, lid, rid)}

        with super_session(super_engine()) as s:
            tenant_id = step_create_tenant(config, s)

        with super_session(super_engine()) as s:
            client_group_ids = step_create_users_and_groups(config, tenant_id, s)
            step_create_mappings(config, catalog_ids, tenant_id, client_group_ids, s)
            sub_ids = step_create_subscriptions(config, catalog_ids, tenant_id, s)
            statuses = step_activate_subscriptions(sub_ids, catalog_ids, tenant_id, s)

        key = ("onb_test_sc", "ONB1", "test_rate")
        assert statuses[key] == "active"

        with super_session(super_engine()) as s:
            ts = s.get(TenantSubscription, sub_ids[key])
            assert ts.status == "active"
    finally:
        _cleanup(super_db_session, pid, tenant_id, canonical_type_ids=ct_ids)


def test_leaves_subscription_pending_when_no_mappings_exist(
    super_db_session, tmp_path
):
    pid, lid, rid = _setup_catalog(super_db_session)
    _setup_pvg(super_db_session, pid, lid, rid, "ECMR")
    super_db_session.commit()
    tenant_id = None
    try:
        config = _config_from_yaml(_VALID_YAML, tmp_path)
        catalog_ids = {("onb_test_sc", "ONB1", "test_rate"): (pid, lid, rid)}

        with super_session(super_engine()) as s:
            tenant_id = step_create_tenant(config, s)

        # Create groups but skip mappings — PVG exists but is unmapped
        with super_session(super_engine()) as s:
            client_group_ids = step_create_users_and_groups(config, tenant_id, s)
            sub_ids = step_create_subscriptions(config, catalog_ids, tenant_id, s)
            statuses = step_activate_subscriptions(sub_ids, catalog_ids, tenant_id, s)

        key = ("onb_test_sc", "ONB1", "test_rate")
        assert statuses[key] == "pending_mapping"

        with super_session(super_engine()) as s:
            ts = s.get(TenantSubscription, sub_ids[key])
            assert ts.status == "pending_mapping"
    finally:
        _cleanup(super_db_session, pid, tenant_id)


def test_activates_subscription_with_partial_mappings(
    super_db_session, tmp_path, capsys
):
    pid, lid, rid = _setup_catalog(super_db_session)
    ct_ecmr = CanonicalVehicleType(
        code="ECMR_ONB02", name="Economy Manual ONB02",
        description="Test canonical for ECMR", taxonomy_version=1,
    )
    ct_suv = CanonicalVehicleType(
        code="SUV1_ONB02", name="SUV ONB02",
        description="Test canonical for SUV1", taxonomy_version=1,
    )
    super_db_session.add_all([ct_ecmr, ct_suv])
    super_db_session.flush()
    _setup_pvg(super_db_session, pid, lid, rid, "ECMR", canonical_type_id=ct_ecmr.id)
    _setup_pvg(super_db_session, pid, lid, rid, "SUV1", canonical_type_id=ct_suv.id)
    super_db_session.commit()
    ct_ids = [ct_ecmr.id, ct_suv.id]
    tenant_id = None
    try:
        config = _config_from_yaml(_VALID_YAML, tmp_path)  # maps only ECMR
        catalog_ids = {("onb_test_sc", "ONB1", "test_rate"): (pid, lid, rid)}

        with super_session(super_engine()) as s:
            tenant_id = step_create_tenant(config, s)

        with super_session(super_engine()) as s:
            client_group_ids = step_create_users_and_groups(config, tenant_id, s)
            mapping_count = step_create_mappings(config, catalog_ids, tenant_id, client_group_ids, s)
            sub_ids = step_create_subscriptions(config, catalog_ids, tenant_id, s)
            statuses = step_activate_subscriptions(sub_ids, catalog_ids, tenant_id, s)

        key = ("onb_test_sc", "ONB1", "test_rate")
        assert mapping_count == 1
        assert statuses[key] == "active"

        with super_session(super_engine()) as s:
            ts = s.get(TenantSubscription, sub_ids[key])
            assert ts.status == "active"

        captured = capsys.readouterr()
        assert "[info]" in captured.err
        # [info] lists canonical type codes — SUV1_ONB02 contains "SUV1"
        assert "SUV1" in captured.err
    finally:
        _cleanup(super_db_session, pid, tenant_id, canonical_type_ids=ct_ids)


def test_rolls_back_tenant_when_discovery_fails(super_db_session, tmp_path):
    pid, lid, rid = _setup_catalog(super_db_session)
    super_db_session.commit()
    tenant_id = None
    try:
        config = _config_from_yaml(_VALID_YAML, tmp_path)
        catalog_ids = {("onb_test_sc", "ONB1", "test_rate"): (pid, lid, rid)}

        with super_session(super_engine()) as s:
            tenant_id = step_create_tenant(config, s)

        with super_session(super_engine()) as s:
            assert s.get(Tenant, tenant_id) is not None

        # No entry in providers_json → OnboardingError
        with pytest.raises(OnboardingError):
            step_discovery(
                catalog_ids=catalog_ids,
                providers_json=[],
                session_factory=partial(super_session, super_engine()),
                period_start=_PERIOD_START,
                period_end=_PERIOD_END,
                scraper_factory=_DUMMY_FACTORY,
            )

        # Simulate what the CLI does on failure
        with super_session(super_engine()) as s:
            rollback_tenant(tenant_id, s)

        with super_session(super_engine()) as s:
            assert s.get(Tenant, tenant_id) is None
        tenant_id = None  # already cleaned up
    finally:
        _cleanup(super_db_session, pid, tenant_id)


def test_skips_discovery_when_tuple_has_successful_scrape_run(super_db_session):
    pid, lid, rid = _setup_catalog(super_db_session)
    run = ScrapeRun(
        provider_id=pid,
        provider_location_id=lid,
        provider_rate_id=rid,
        status="success",
    )
    super_db_session.add(run)
    super_db_session.commit()

    catalog_ids = {("onb_test_sc", "ONB1", "test_rate"): (pid, lid, rid)}
    session_factory = partial(super_session, super_engine())

    try:
        with patch.object(
            SmartScraperOrchestrator,
            "_run_session",
            new=AsyncMock(return_value=[]),
        ) as mock_run:
            step_discovery(
                catalog_ids=catalog_ids,
                providers_json=[_PROVIDER_ENTRY],
                session_factory=session_factory,
                period_start=_PERIOD_START,
                period_end=_PERIOD_END,
                scraper_factory=_DUMMY_FACTORY,
            )
        mock_run.assert_not_called()
    finally:
        _cleanup(super_db_session, pid)


def test_runs_discovery_when_tuple_has_no_successful_scrape_run(super_db_session):
    pid, lid, rid = _setup_catalog(super_db_session)
    super_db_session.commit()

    catalog_ids = {("onb_test_sc", "ONB1", "test_rate"): (pid, lid, rid)}
    session_factory = partial(super_session, super_engine())

    try:
        with patch.object(
            SmartScraperOrchestrator,
            "_run_session",
            new=AsyncMock(return_value=[]),
        ) as mock_run:
            step_discovery(
                catalog_ids=catalog_ids,
                providers_json=[_PROVIDER_ENTRY],
                session_factory=session_factory,
                period_start=_PERIOD_START,
                period_end=_PERIOD_END,
                scraper_factory=_DUMMY_FACTORY,
            )
        mock_run.assert_called()
    finally:
        _cleanup(super_db_session, pid)


def test_fails_loud_on_duplicate_tenant_run(super_db_session, tmp_path):
    config = _config_from_yaml(_VALID_YAML, tmp_path)
    step_create_tenant(config, super_db_session)
    super_db_session.flush()

    with pytest.raises(OnboardingError, match="already exists"):
        step_create_tenant(config, super_db_session)


def test_activates_subscription_when_partial_scope_declared_explicitly(
    super_db_session, tmp_path, capsys
):
    """Provider has 5 PVGs; tenant maps only 2. Subscription must activate and
    [info] must mention the 3 out-of-scope groups."""
    import textwrap as _tw
    pid, lid, rid = _setup_catalog(super_db_session)
    # Seed one canonical type per PVC so all are classified before onboarding
    cts = []
    for code in ("PVG1", "PVG2", "PVG3", "PVG4", "PVG5"):
        ct = CanonicalVehicleType(
            code=f"{code}_ONB08", name=f"{code} ONB08",
            description=f"Test canonical for {code}", taxonomy_version=1,
        )
        super_db_session.add(ct)
        cts.append(ct)
    super_db_session.flush()
    for ct, ext_code in zip(cts, ("PVG1", "PVG2", "PVG3", "PVG4", "PVG5")):
        _setup_pvg(super_db_session, pid, lid, rid, ext_code, canonical_type_id=ct.id)
    super_db_session.commit()
    ct_ids = [ct.id for ct in cts]

    yaml_text = _tw.dedent("""\
        tenant:
          name: Partial Scope Test
          currency: EUR
          owner_email: owner@partial.com

        vehicle_groups:
          - code: compact
            name: Compact
            display_order: 1
          - code: suv
            name: SUV
            display_order: 2

        subscriptions:
          - provider_code: onb_test_sc
            location_code: ONB1
            rate_code: test_rate
            mappings:
              - client_group_code: compact
                external_codes:
                  - PVG1
              - client_group_code: suv
                external_codes:
                  - PVG2
    """)

    tenant_id = None
    try:
        config = _config_from_yaml(yaml_text, tmp_path)
        catalog_ids = {("onb_test_sc", "ONB1", "test_rate"): (pid, lid, rid)}

        with super_session(super_engine()) as s:
            tenant_id = step_create_tenant(config, s)

        with super_session(super_engine()) as s:
            client_group_ids = step_create_users_and_groups(config, tenant_id, s)
            step_create_mappings(config, catalog_ids, tenant_id, client_group_ids, s)
            sub_ids = step_create_subscriptions(config, catalog_ids, tenant_id, s)
            statuses = step_activate_subscriptions(sub_ids, catalog_ids, tenant_id, s)

        key = ("onb_test_sc", "ONB1", "test_rate")
        assert statuses[key] == "active"

        with super_session(super_engine()) as s:
            ts = s.get(TenantSubscription, sub_ids[key])
            assert ts.status == "active"

        captured = capsys.readouterr()
        assert "[info]" in captured.err
        # [info] lists canonical type codes — PVGn_ONB08 codes contain "PVGn"
        assert all(code in captured.err for code in ("PVG3", "PVG4", "PVG5"))
    finally:
        _cleanup(super_db_session, pid, tenant_id, canonical_type_ids=ct_ids)


def test_validate_catalog_handles_multiple_subscriptions_to_same_provider(
    super_db_session, tmp_path
):
    # Build a provider with two locations manually (CatalogSyncService de-dupes by name,
    # so we insert directly to get independent IDs back)
    provider = Provider(
        code="onb_test_sc",
        display_name="Onboard Test Provider",
        scraper_key="onb_test_sc",
        default_currency="EUR",
        status="active",
    )
    super_db_session.add(provider)
    super_db_session.flush()

    loc1 = ProviderLocation(
        provider_id=provider.id, location_code="ONB1",
        location_name="Location 1", active=True,
    )
    loc2 = ProviderLocation(
        provider_id=provider.id, location_code="ONB2",
        location_name="Location 2", active=True,
    )
    rate = ProviderRate(
        provider_id=provider.id, rate_code="test_rate",
        rate_name="Test Rate", active=True,
    )
    super_db_session.add_all([loc1, loc2, rate])
    super_db_session.flush()

    yaml_text = textwrap.dedent("""\
        tenant:
          name: Multi-Sub Test
          currency: EUR
          owner_email: owner@test.com

        vehicle_groups:
          - code: compact
            name: Compact
            display_order: 1

        subscriptions:
          - provider_code: onb_test_sc
            location_code: ONB1
            rate_code: test_rate
            mappings:
              - client_group_code: compact
                external_codes: [ECMR]
          - provider_code: onb_test_sc
            location_code: ONB2
            rate_code: test_rate
            mappings:
              - client_group_code: compact
                external_codes: [ECMR]
    """)
    config = _config_from_yaml(yaml_text, tmp_path)

    result = step_validate_catalog(config, super_db_session)

    assert ("onb_test_sc", "ONB1", "test_rate") in result
    assert ("onb_test_sc", "ONB2", "test_rate") in result
    pid1, lid1, rid1 = result[("onb_test_sc", "ONB1", "test_rate")]
    pid2, lid2, rid2 = result[("onb_test_sc", "ONB2", "test_rate")]
    assert pid1 == pid2      # same provider
    assert lid1 != lid2      # different locations
    assert rid1 == rid2      # same rate


def test_step_create_mappings_rejects_unclassified_pvc(
    super_db_session, tmp_path
):
    """step_create_mappings raises OnboardingError when a referenced PVC has no canonical_type_id."""
    pid, lid, rid = _setup_catalog(super_db_session)
    _setup_pvg(super_db_session, pid, lid, rid, "ECMR", canonical_type_id=None)
    super_db_session.commit()
    tenant_id = None
    try:
        config = _config_from_yaml(_VALID_YAML, tmp_path)
        catalog_ids = {("onb_test_sc", "ONB1", "test_rate"): (pid, lid, rid)}

        with super_session(super_engine()) as s:
            tenant_id = step_create_tenant(config, s)

        with super_session(super_engine()) as s:
            client_group_ids = step_create_users_and_groups(config, tenant_id, s)
            with pytest.raises(OnboardingError, match="not yet classified"):
                step_create_mappings(config, catalog_ids, tenant_id, client_group_ids, s)
    finally:
        _cleanup(super_db_session, pid, tenant_id)
