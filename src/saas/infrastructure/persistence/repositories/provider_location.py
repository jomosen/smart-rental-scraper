from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.catalog import ProviderLocation


class ProviderLocationRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def get_by_provider_and_code(self, provider_id: int, location_code: str) -> Optional[ProviderLocation]:
        return self._s.scalar(
            select(ProviderLocation).where(
                ProviderLocation.provider_id == provider_id,
                ProviderLocation.location_code == location_code,
            )
        )

    def list_for_provider(self, provider_id: int) -> list[ProviderLocation]:
        return list(
            self._s.scalars(
                select(ProviderLocation)
                .where(ProviderLocation.provider_id == provider_id)
                .order_by(ProviderLocation.location_code)
            )
        )
