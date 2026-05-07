from __future__ import annotations

import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.catalog import ProviderVehicleGroup


class ProviderVehicleGroupRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def get_by_external_code(
        self,
        provider_id: int,
        provider_location_id: int,
        provider_rate_id: int,
        external_code: str,
    ) -> Optional[ProviderVehicleGroup]:
        return self._s.scalar(
            select(ProviderVehicleGroup).where(
                ProviderVehicleGroup.provider_id == provider_id,
                ProviderVehicleGroup.provider_location_id == provider_location_id,
                ProviderVehicleGroup.provider_rate_id == provider_rate_id,
                ProviderVehicleGroup.external_code == external_code,
            )
        )

    def upsert_seen(
        self,
        provider_id: int,
        provider_location_id: int,
        provider_rate_id: int,
        external_code: str,
        external_name: str,
    ) -> ProviderVehicleGroup:
        """Return the existing row if found, updating last_seen_at; insert otherwise."""
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        row = self.get_by_external_code(
            provider_id, provider_location_id, provider_rate_id, external_code
        )
        if row is None:
            row = ProviderVehicleGroup(
                provider_id=provider_id,
                provider_location_id=provider_location_id,
                provider_rate_id=provider_rate_id,
                external_code=external_code,
                external_name=external_name,
                first_seen_at=now,
                last_seen_at=now,
            )
            self._s.add(row)
            self._s.flush()  # populate row.id and make the row visible to subsequent queries
        else:
            row.last_seen_at = now
            if row.external_name != external_name:
                row.external_name = external_name
        return row
