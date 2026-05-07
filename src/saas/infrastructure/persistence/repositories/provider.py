from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.catalog import Provider


class ProviderRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def get_by_code(self, code: str) -> Optional[Provider]:
        return self._s.scalar(select(Provider).where(Provider.code == code))

    def list_active(self) -> list[Provider]:
        return list(
            self._s.scalars(
                select(Provider).where(Provider.status == "active").order_by(Provider.code)
            )
        )
