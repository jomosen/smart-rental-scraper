from __future__ import annotations

from decimal import Decimal
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..models.catalog import ModelClassification


class ModelClassificationRepository:
    """Read-through cache for ad-hoc model → ACRISS classifications.

    Caller owns the transaction (commit/rollback).
    """

    def __init__(self, session: Session) -> None:
        self._s = session

    def get(self, normalized_model: str, classifier_version: str) -> Optional[ModelClassification]:
        return self._s.scalar(
            select(ModelClassification).where(
                ModelClassification.normalized_model == normalized_model,
                ModelClassification.classifier_version == classifier_version,
            )
        )

    def touch(self, normalized_model: str, classifier_version: str) -> None:
        """Record a cache hit (bump last_used_at + hit_count)."""
        self._s.execute(
            text(
                "UPDATE model_classifications "
                "SET last_used_at = NOW(), hit_count = hit_count + 1 "
                "WHERE normalized_model = :m AND classifier_version = :v"
            ),
            {"m": normalized_model, "v": classifier_version},
        )

    def upsert(
        self,
        normalized_model: str,
        classifier_version: str,
        acriss_code: Optional[str],
        confidence: float,
        pending_review: bool,
        acriss_full: Optional[str] = None,
    ) -> None:
        self._s.execute(
            text(
                "INSERT INTO model_classifications "
                "  (normalized_model, classifier_version, acriss_code, acriss_full, "
                "   confidence, pending_review) "
                "VALUES (:m, :v, :code, :full, :conf, :pending) "
                "ON CONFLICT (normalized_model, classifier_version) DO UPDATE "
                "SET acriss_code = EXCLUDED.acriss_code, "
                "    acriss_full = EXCLUDED.acriss_full, "
                "    confidence = EXCLUDED.confidence, "
                "    pending_review = EXCLUDED.pending_review, "
                "    last_used_at = NOW()"
            ),
            {
                "m": normalized_model,
                "v": classifier_version,
                "code": acriss_code,
                "full": acriss_full,
                "conf": Decimal(str(round(confidence, 3))),
                "pending": pending_review,
            },
        )
