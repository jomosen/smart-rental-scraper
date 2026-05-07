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
