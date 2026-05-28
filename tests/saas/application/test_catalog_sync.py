"""Integration tests for CatalogSyncService.

Requires the local Postgres DB to be running with migrations applied.
Each test rolls back via the super_db_session fixture (see conftest.py).
"""
from __future__ import annotations

import pytest

from src.saas.application.catalog_sync import CatalogSyncService, _slugify
from src.saas.infrastructure.persistence.models.catalog import Provider
from src.saas.infrastructure.persistence.repositories import (
    ProviderLocationRepository,
    ProviderRateRepository,
    ProviderRepository,
)
from src.scraper.application.builder.provision import ProviderProvisioningService

_BASE_ENTRY = {
    "name": "Sync Test Provider",
    "scraper": "sync_test_scraper",
    "base_url": "https://sync-test.example.com",
    "location_id": "SYN1",
    "location_name": "Sync Test Location",
    "rate_name": "Standard Rate",
    "enabled": True,
}


class TestCatalogSync:
    def test_sync_from_empty_db_creates_all(self, super_db_session):
        service = CatalogSyncService(super_db_session)
        catalog_ids = service.sync_from_providers_json([_BASE_ENTRY])

        assert "Sync Test Provider" in catalog_ids
        pid, lid, rid = catalog_ids["Sync Test Provider"]
        assert pid is not None
        assert lid is not None
        assert rid is not None

        provider = super_db_session.get(Provider, pid)
        assert provider is not None
        assert provider.code == "sync_test_scraper"
        assert provider.display_name == "Sync Test Provider"
        assert provider.default_currency == "EUR"

    def test_sync_idempotent_does_not_duplicate(self, super_db_session):
        service = CatalogSyncService(super_db_session)
        ids1 = service.sync_from_providers_json([_BASE_ENTRY])
        ids2 = service.sync_from_providers_json([_BASE_ENTRY])

        pid1, lid1, rid1 = ids1["Sync Test Provider"]
        pid2, lid2, rid2 = ids2["Sync Test Provider"]

        assert pid1 == pid2
        assert lid1 == lid2
        assert rid1 == rid2

    def test_sync_updates_display_name_when_changed(self, super_db_session):
        entry_v1 = {**_BASE_ENTRY, "name": "Old Display Name", "scraper": "update_test_sc"}
        entry_v2 = {**_BASE_ENTRY, "name": "New Display Name", "scraper": "update_test_sc"}

        service = CatalogSyncService(super_db_session)
        ids1 = service.sync_from_providers_json([entry_v1])
        ids2 = service.sync_from_providers_json([entry_v2])

        pid1, _, _ = ids1["Old Display Name"]
        pid2, _, _ = ids2["New Display Name"]
        assert pid1 == pid2

        provider = super_db_session.get(Provider, pid1)
        assert provider.display_name == "New Display Name"


_TARGETS = {
    "location": "Alicante",
    "pickup_date": "2026-07-01",
    "return_date": "2026-07-08",
    "pickup_time": "10:00",
    "return_time": "10:00",
}


class TestProviderProvisioningService:
    def _svc(self, session) -> ProviderProvisioningService:
        return ProviderProvisioningService(
            provider_repo=ProviderRepository(session),
            location_repo=ProviderLocationRepository(session),
            rate_repo=ProviderRateRepository(session),
        )

    def test_ensure_creates_all_rows(self, super_db_session):
        svc = self._svc(super_db_session)
        result = svc.ensure("prov_pps_new", _TARGETS)

        assert result.provider_id is not None
        assert result.location_id is not None
        assert result.rate_id is not None

        provider = super_db_session.get(Provider, result.provider_id)
        assert provider.code == "prov_pps_new"
        assert provider.status == "active"

    def test_ensure_is_idempotent(self, super_db_session):
        svc = self._svc(super_db_session)
        r1 = svc.ensure("prov_pps_idem", _TARGETS)
        r2 = svc.ensure("prov_pps_idem", _TARGETS)

        assert r1.provider_id == r2.provider_id
        assert r1.location_id == r2.location_id
        assert r1.rate_id == r2.rate_id

    def test_ensure_location_code_is_slugified(self, super_db_session):
        svc = self._svc(super_db_session)
        targets = {**_TARGETS, "location": "Las Palmas"}
        result = svc.ensure("prov_pps_slug", targets)

        from src.saas.infrastructure.persistence.models.catalog import ProviderLocation
        loc = super_db_session.get(ProviderLocation, result.location_id)
        assert loc.location_code == "las_palmas"
        assert loc.location_name == "Las Palmas"

    def test_ensure_default_rate_is_standard(self, super_db_session):
        svc = self._svc(super_db_session)
        result = svc.ensure("prov_pps_rate", _TARGETS)

        from src.saas.infrastructure.persistence.models.catalog import ProviderRate
        rate = super_db_session.get(ProviderRate, result.rate_id)
        assert rate.rate_code == "standard"
