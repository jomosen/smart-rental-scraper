from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..models.catalog import HomogeneousZone


class HomogeneousZoneRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def leading_active_zone(
        self,
        provider_id: int,
        provider_location_id: int,
        provider_rate_id: int,
    ) -> Optional[tuple[datetime.date, Optional[Decimal]]]:
        """Return (start_date, reference_price) of the earliest active zone for the
        tuple, or None if there are none.

        Zones share boundaries + reference_price across vehicle categories (they are
        provider-level), so the earliest active zone identifies the leading season.
        Used to carry a stable season start_date forward across scrapes.
        """
        row = self._s.execute(
            select(HomogeneousZone.start_date, HomogeneousZone.reference_price)
            .where(
                HomogeneousZone.provider_id == provider_id,
                HomogeneousZone.provider_location_id == provider_location_id,
                HomogeneousZone.provider_rate_id == provider_rate_id,
                HomogeneousZone.active.is_(True),
            )
            .order_by(HomogeneousZone.start_date)
            .limit(1)
        ).first()
        return (row.start_date, row.reference_price) if row else None

    def replace_zones_for_tuple(
        self,
        provider_id: int,
        provider_location_id: int,
        provider_rate_id: int,
        provider_vehicle_category_id: int,
        new_zones: list[HomogeneousZone],
    ) -> None:
        """Deactivate existing active zones for the tuple, then add the new ones.

        All changes are flushed within the caller's transaction; the caller
        is responsible for commit/rollback.
        """
        self._s.execute(
            update(HomogeneousZone)
            .where(
                HomogeneousZone.provider_id == provider_id,
                HomogeneousZone.provider_location_id == provider_location_id,
                HomogeneousZone.provider_rate_id == provider_rate_id,
                HomogeneousZone.provider_vehicle_category_id == provider_vehicle_category_id,
                HomogeneousZone.active.is_(True),
            )
            .values(active=False)
        )
        for zone in new_zones:
            self._s.add(zone)
        self._s.flush()
