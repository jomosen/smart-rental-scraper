from __future__ import annotations

import datetime
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.catalog import CanonicalVehicleType


class CanonicalVehicleTypeRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def get_by_code(self, code: str) -> Optional[CanonicalVehicleType]:
        return self._s.scalar(
            select(CanonicalVehicleType).where(CanonicalVehicleType.code == code)
        )

    def get_all_active(self) -> List[CanonicalVehicleType]:
        return list(self._s.scalars(
            select(CanonicalVehicleType).where(CanonicalVehicleType.active.is_(True))
        ).all())

    def upsert(
        self,
        code: str,
        name: str,
        description: str,
        taxonomy_version: int,
        family: str = "",
    ) -> CanonicalVehicleType:
        """Insert or update a canonical vehicle type by code.

        Used by the seed script.  Idempotent: running twice on the same YAML
        produces zero net changes.
        """
        row = self.get_by_code(code)
        if row is None:
            row = CanonicalVehicleType(
                code=code,
                family=family,
                name=name,
                description=description,
                taxonomy_version=taxonomy_version,
                active=True,
            )
            self._s.add(row)
            self._s.flush()
        else:
            row.family = family
            row.name = name
            row.description = description
            row.taxonomy_version = taxonomy_version
        return row

    def mark_deprecated(self, code: str) -> None:
        """Mark a canonical type as inactive.  Does not delete the row."""
        row = self.get_by_code(code)
        if row is not None and row.active:
            row.active = False
            row.deprecated_at = datetime.datetime.now(tz=datetime.timezone.utc)
