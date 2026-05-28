from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.catalog import ProviderRate


class ProviderRateRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def get_by_provider_and_code(self, provider_id: int, rate_code: str) -> Optional[ProviderRate]:
        return self._s.scalar(
            select(ProviderRate).where(
                ProviderRate.provider_id == provider_id,
                ProviderRate.rate_code == rate_code,
            )
        )

    def get_or_create(
        self,
        provider_id: int,
        rate_code: str,
        rate_name: str,
    ) -> ProviderRate:
        """Get existing rate or create it. Updates rate_name if changed."""
        rate = self.get_by_provider_and_code(provider_id, rate_code)
        if rate is None:
            rate = ProviderRate(
                provider_id=provider_id,
                rate_code=rate_code,
                rate_name=rate_name,
                active=True,
            )
            self._s.add(rate)
            self._s.flush()
        elif rate.rate_name != rate_name:
            rate.rate_name = rate_name
            self._s.flush()
        return rate
