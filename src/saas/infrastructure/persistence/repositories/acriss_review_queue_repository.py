"""AcrissReviewQueueRepository — unknown models awaiting operator validation.

Catalog scope (no tenant). Upsert semantics: one row per normalized_model;
repeat sightings refresh last_seen_at, accumulate sources_seen and update the
suggestion — but NEVER touch rows already 'accepted' or 'rejected' (the
operator's verdict is final until they change it). See DATA_MODEL.md
Decision 12.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.catalog import AcrissReviewQueueEntry


class AcrissReviewQueueRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def upsert_sighting(
        self,
        normalized_model: str,
        raw_model: str,
        source: str,
        suggested_category: Optional[str] = None,
        suggested_type: Optional[str] = None,
        suggested_powertrain: Optional[str] = None,
        suggested_acriss: Optional[str] = None,
        confidence: Optional[float] = None,
        reason: Optional[str] = None,
    ) -> AcrissReviewQueueEntry:
        row = self._s.scalar(
            select(AcrissReviewQueueEntry).where(
                AcrissReviewQueueEntry.normalized_model == normalized_model
            )
        )
        if row is None:
            row = AcrissReviewQueueEntry(
                normalized_model=normalized_model,
                raw_model=raw_model,
                suggested_category=suggested_category,
                suggested_type=suggested_type,
                suggested_powertrain=suggested_powertrain,
                suggested_acriss=suggested_acriss,
                confidence=confidence,
                reason=reason,
                sources_seen=[source],
            )
            self._s.add(row)
            self._s.flush()
            return row

        from sqlalchemy import func
        row.last_seen_at = func.now()
        if source not in (row.sources_seen or []):
            row.sources_seen = list(row.sources_seen or []) + [source]
        if row.status == "pending_review":
            row.suggested_category = suggested_category or row.suggested_category
            row.suggested_type = suggested_type or row.suggested_type
            row.suggested_powertrain = suggested_powertrain or row.suggested_powertrain
            row.suggested_acriss = suggested_acriss or row.suggested_acriss
            if confidence is not None:
                row.confidence = confidence
            if reason:
                row.reason = reason
        self._s.flush()
        return row

    def list_pending(self) -> list[AcrissReviewQueueEntry]:
        return list(
            self._s.scalars(
                select(AcrissReviewQueueEntry)
                .where(AcrissReviewQueueEntry.status == "pending_review")
                .order_by(AcrissReviewQueueEntry.first_seen_at)
            )
        )

    def set_status(self, normalized_model: str, status: str) -> bool:
        row = self._s.scalar(
            select(AcrissReviewQueueEntry).where(
                AcrissReviewQueueEntry.normalized_model == normalized_model
            )
        )
        if row is None:
            return False
        row.status = status
        self._s.flush()
        return True
