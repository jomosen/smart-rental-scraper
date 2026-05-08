from __future__ import annotations

import datetime
from typing import Optional

from sqlalchemy import update
from sqlalchemy.orm import Session

from ..models.catalog import ScrapeRun


class ScrapeRunRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def create(
        self,
        provider_id: int,
        provider_location_id: int,
        provider_rate_id: int,
    ) -> ScrapeRun:
        run = ScrapeRun(
            provider_id=provider_id,
            provider_location_id=provider_location_id,
            provider_rate_id=provider_rate_id,
            status="running",
        )
        self._s.add(run)
        self._s.flush()  # populate run.id before returning
        return run

    def mark_finished(
        self,
        run_id: int,
        status: str,
        stats: Optional[dict] = None,
        error: Optional[str] = None,
    ) -> None:
        """Update the run record by ID. Safe to call from a different session than create()."""
        self._s.execute(
            update(ScrapeRun)
            .where(ScrapeRun.id == run_id)
            .values(
                finished_at=datetime.datetime.now(tz=datetime.timezone.utc),
                status=status,
                stats_jsonb=stats,
                error=error,
            )
        )
