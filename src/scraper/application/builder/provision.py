"""ProviderProvisioningService — ensure catalog rows exist before saving a recipe.

Single responsibility: given (provider_key, targets), make sure provider +
provider_location + provider_rate exist in the DB catalog.  Idempotent.

No Playwright, no LLM, no recipe logic — pure catalog bookkeeping.
Depends on injected repository instances; caller owns session lifecycle.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ....saas.infrastructure.persistence.repositories import (
        ProviderLocationRepository,
        ProviderRateRepository,
        ProviderRepository,
    )


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


@dataclass(frozen=True)
class ProvisioningResult:
    provider_id: int
    location_id: int
    rate_id: int


class ProviderProvisioningService:
    """Ensure provider + location + rate catalog rows exist for a discovery run.

    All three repos must be bound to the same open Session.  flush() is called
    by the repo methods; commit/rollback is the caller's responsibility.
    """

    def __init__(
        self,
        provider_repo: "ProviderRepository",
        location_repo: "ProviderLocationRepository",
        rate_repo: "ProviderRateRepository",
    ) -> None:
        self._provider_repo = provider_repo
        self._location_repo = location_repo
        self._rate_repo = rate_repo

    def ensure(
        self,
        provider_key: str,
        targets: dict,
        base_url: str = "",
    ) -> ProvisioningResult:
        """Idempotently create/get provider + location + rate rows.

        provider_key — e.g. "centauro"; becomes providers.code
        targets      — scrape targets dict; targets["location"] drives location_code
        base_url     — entry URL for the provider's booking site; stored in DB
                       and used by the pipeline when base_url is set in providers.
        """
        provider = self._provider_repo.get_or_create(
            code=provider_key,
            display_name=provider_key.capitalize(),
            scraper_key=provider_key,
            currency="EUR",
            base_url=base_url,
        )

        location_name = targets["location"]
        # location_code is VARCHAR(16) in the catalog; slugs of multi-word
        # location names ("Alicante Aeropuerto") exceed it. Truncation is safe:
        # the code is an internal identifier, and get_or_create dedupes on the
        # same truncated value on every rerun.
        location = self._location_repo.get_or_create(
            provider_id=provider.id,
            location_code=_slugify(location_name)[:16],
            location_name=location_name,
        )

        rate = self._rate_repo.get_or_create(
            provider_id=provider.id,
            rate_code="standard",
            rate_name="Standard",
        )

        return ProvisioningResult(
            provider_id=provider.id,
            location_id=location.id,
            rate_id=rate.id,
        )
