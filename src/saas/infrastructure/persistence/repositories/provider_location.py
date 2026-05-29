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

    def get_or_create(
        self,
        provider_id: int,
        location_code: str,
        location_name: str,
    ) -> ProviderLocation:
        """Get existing location or create it. Updates location_name if changed."""
        location = self.get_by_provider_and_code(provider_id, location_code)
        if location is None:
            location = ProviderLocation(
                provider_id=provider_id,
                location_code=location_code,
                location_name=location_name,
                active=True,
            )
            self._s.add(location)
            self._s.flush()
        elif location.location_name != location_name:
            location.location_name = location_name
            self._s.flush()
        return location

    def list_for_provider(self, provider_id: int) -> list[ProviderLocation]:
        return list(
            self._s.scalars(
                select(ProviderLocation)
                .where(
                    ProviderLocation.provider_id == provider_id,
                    ProviderLocation.active.is_(True),
                )
                .order_by(ProviderLocation.location_code)
            )
        )
