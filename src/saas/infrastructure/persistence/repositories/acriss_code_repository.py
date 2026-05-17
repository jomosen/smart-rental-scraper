from __future__ import annotations

import datetime
import json
import logging
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.catalog import AcrissCode

logger = logging.getLogger(__name__)


class AcrissCodeRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def get_by_code(self, code: str) -> Optional[AcrissCode]:
        return self._s.scalar(
            select(AcrissCode).where(AcrissCode.code == code)
        )

    def list_active(self) -> List[AcrissCode]:
        return list(
            self._s.scalars(select(AcrissCode).where(AcrissCode.active.is_(True))).all()
        )

    def upsert(
        self,
        code: str,
        display_name: str,
        description: str,
        criteria: list,
        examples: list,
        active: bool = True,
    ) -> AcrissCode:
        if len(code) != 4:
            raise ValueError(f"ACRISS code must be exactly 4 characters, got: {code!r}")

        now = datetime.datetime.now(tz=datetime.timezone.utc)
        row = self.get_by_code(code)
        if row is None:
            row = AcrissCode(
                code=code,
                acriss_category=code[0],
                acriss_body_type=code[1],
                acriss_transmission=code[2],
                acriss_fuel=code[3],
                display_name=display_name,
                description=description,
                criteria=criteria,
                examples=examples,
                active=active,
                created_at=now,
                last_updated_at=now,
            )
            self._s.add(row)
        else:
            row.display_name = display_name
            row.description = description
            row.criteria = criteria
            row.examples = examples
            row.active = active
            row.last_updated_at = now

        self._s.flush()
        return row

    def deactivate_missing(self, active_codes: set[str]) -> int:
        """Mark active rows whose code is not in `active_codes` as inactive.

        Returns the count of rows deactivated.
        """
        rows = self.list_active()
        count = 0
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        for row in rows:
            if row.code not in active_codes:
                row.active = False
                row.last_updated_at = now
                count += 1
        if count:
            self._s.flush()
        return count
