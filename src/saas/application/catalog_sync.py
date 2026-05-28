"""CatalogSyncService — bootstrap the provider catalog from providers.json.

Called once at scraper startup (before any scraping begins) to ensure every
provider, location, and rate referenced in providers.json exists in the DB.
Idempotent: safe to call on every startup without creating duplicates.
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from ..infrastructure.persistence.models.catalog import (
    Provider,
    ProviderLocation,
    ProviderRate,
)  # kept for return type annotations
from ..infrastructure.persistence.repositories import (
    ProviderLocationRepository,
    ProviderRateRepository,
    ProviderRepository,
)


def _slugify(name: str) -> str:
    """Convert a display name to a stable, URL-safe code. E.g. 'Standard Rate' → 'standard_rate'."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


class CatalogSyncService:
    """Upsert provider catalog rows from providers.json entries.

    Uses flush() after each upsert so that generated IDs are available
    immediately within the same transaction. The caller is responsible for
    commit/rollback.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._provider_repo = ProviderRepository(session)
        self._location_repo = ProviderLocationRepository(session)
        self._rate_repo = ProviderRateRepository(session)

    def sync_from_providers_json(
        self, providers_json: list[dict]
    ) -> dict[str, tuple[int, int, int]]:
        """Upsert providers, locations, and rates for every enabled entry.

        Returns a mapping of ``entry['name'] → (provider_id, location_id, rate_id)``
        for all enabled providers. Disabled entries are silently skipped.
        """
        catalog_ids: dict[str, tuple[int, int, int]] = {}

        for entry in providers_json:
            if not entry.get("enabled", True):
                continue

            provider = self._upsert_provider(entry)
            location = self._upsert_location(entry, provider.id)
            rate = self._upsert_rate(entry, provider.id)

            catalog_ids[entry["name"]] = (provider.id, location.id, rate.id)

        return catalog_ids

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _upsert_provider(self, entry: dict) -> Provider:
        return self._provider_repo.get_or_create(
            code=entry["scraper"],
            display_name=entry["name"],
            scraper_key=entry["scraper"],
            currency="EUR",
        )

    def _upsert_location(self, entry: dict, provider_id: int) -> ProviderLocation:
        return self._location_repo.get_or_create(
            provider_id=provider_id,
            location_code=entry["location_id"],
            location_name=entry["location_name"],
        )

    def _upsert_rate(self, entry: dict, provider_id: int) -> ProviderRate:
        return self._rate_repo.get_or_create(
            provider_id=provider_id,
            rate_code=_slugify(entry["rate_name"]),
            rate_name=entry["rate_name"],
        )
