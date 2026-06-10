from __future__ import annotations

import datetime
import hashlib
import logging
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.catalog import ProviderVehicleCategory
from ....application.classification.dtos import ClassificationResult

logger = logging.getLogger(__name__)


def attributes_hash(
    example_models: str,
    seats: Optional[int],
    luggage: Optional[int],
) -> str:
    """SHA256 truncated to 16 hex chars over stable vehicle identity attributes.

    Shared with callers (e.g. the scraper orchestrator) that need to decide
    whether a group's attributes changed since the last run — and therefore
    whether it must be re-sent to the (paid) classifier — without re-deriving
    the key format. Deliberately excludes price: a price move is not a reason
    to reclassify.
    """
    key = "|".join([
        example_models or "",
        str(seats) if seats is not None else "",
        str(luggage) if luggage is not None else "",
    ])
    return hashlib.sha256(key.encode()).hexdigest()[:16]


class ProviderVehicleCategoryRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def get_by_external_code(
        self,
        provider_id: int,
        provider_location_id: int,
        provider_rate_id: int,
        external_code: str,
    ) -> Optional[ProviderVehicleCategory]:
        return self._s.scalar(
            select(ProviderVehicleCategory).where(
                ProviderVehicleCategory.provider_id == provider_id,
                ProviderVehicleCategory.provider_location_id == provider_location_id,
                ProviderVehicleCategory.provider_rate_id == provider_rate_id,
                ProviderVehicleCategory.external_code == external_code,
            )
        )

    def list_active_for_tuple(
        self,
        provider_id: int,
        provider_location_id: int,
        provider_rate_id: int,
    ) -> List[ProviderVehicleCategory]:
        return list(self._s.scalars(
            select(ProviderVehicleCategory).where(
                ProviderVehicleCategory.provider_id == provider_id,
                ProviderVehicleCategory.provider_location_id == provider_location_id,
                ProviderVehicleCategory.provider_rate_id == provider_rate_id,
                ProviderVehicleCategory.active == True,
            )
        ).all())

    def upsert_seen(
        self,
        provider_id: int,
        provider_location_id: int,
        provider_rate_id: int,
        external_code: Optional[str],
        external_name: Optional[str],
        example_models: str,
        seats: Optional[int],
        luggage: Optional[int],
        classification: ClassificationResult,
        transmission: Optional[str] = None,
    ) -> ProviderVehicleCategory:
        """Find or create a PVC, apply the given ACRISS classification, and update attributes.

        Identity:
          - external_code is not None → unique by (provider, location, rate, external_code)
          - external_code is None     → unique by (provider, location, rate, attributes_hash)

        Classification is always applied.
        """
        now = datetime.datetime.now(tz=datetime.timezone.utc)

        if external_code is not None:
            pvc = self.get_by_external_code(
                provider_id, provider_location_id, provider_rate_id, external_code
            )
            row_hash: Optional[str] = None
        else:
            row_hash = self._compute_hash(example_models, seats, luggage)
            pvc = self._get_by_attributes_hash(
                provider_id, provider_location_id, provider_rate_id, row_hash
            )

        is_new = pvc is None

        if is_new:
            pvc = ProviderVehicleCategory(
                provider_id=provider_id,
                provider_location_id=provider_location_id,
                provider_rate_id=provider_rate_id,
                external_code=external_code,
                external_name=external_name,
                example_models=example_models,
                seats=seats,
                luggage=luggage,
                transmission=transmission,
                attributes_hash=row_hash,
                active=True,
                first_seen_at=now,
                last_seen_at=now,
            )
            self._s.add(pvc)
        else:
            pvc.last_seen_at = now
            pvc.example_models = example_models
            pvc.seats = seats
            pvc.luggage = luggage
            if external_name is not None:
                pvc.external_name = external_name
            if transmission is not None:
                pvc.transmission = transmission

        self._apply_classification(pvc, classification, is_new)
        self._s.flush()
        return pvc

    # ── Private ──────────────────────────────────────────────────────────────

    @staticmethod
    def _compute_hash(
        example_models: str,
        seats: Optional[int],
        luggage: Optional[int],
    ) -> str:
        return attributes_hash(example_models, seats, luggage)

    def _get_by_attributes_hash(
        self,
        provider_id: int,
        provider_location_id: int,
        provider_rate_id: int,
        attributes_hash: str,
    ) -> Optional[ProviderVehicleCategory]:
        return self._s.scalar(
            select(ProviderVehicleCategory).where(
                ProviderVehicleCategory.provider_id == provider_id,
                ProviderVehicleCategory.provider_location_id == provider_location_id,
                ProviderVehicleCategory.provider_rate_id == provider_rate_id,
                ProviderVehicleCategory.external_code.is_(None),
                ProviderVehicleCategory.attributes_hash == attributes_hash,
            )
        )

    def _apply_classification(
        self,
        pvc: ProviderVehicleCategory,
        result: ClassificationResult,
        is_new: bool,
    ) -> None:
        """Write ACRISS classification result onto the PVC row.

        Pending-review policy:
          - Existing PVC with a previous ACRISS classification: keep the cached
            classification, mark pending_review=True so the operator knows
            confidence dropped.
          - New PVC (or existing with no prior code) + LLM returned a candidate
            code (e.g. mixed group resolved via rule 4): persist the code and
            mark pending_review=True for operator review.
          - New PVC (or existing with no prior code) + no candidate code: NULL
            ACRISS attrs, pending_review=True.
        """
        if result.pending_review:
            if not is_new and pvc.acriss_category is not None:
                pvc.classification_confidence = result.confidence
                pvc.pending_review = True
                return

            if result.acriss_category is not None:
                pvc.acriss_category = result.acriss_category
                pvc.acriss_body_type = result.acriss_body_type
                pvc.acriss_transmission = result.acriss_transmission
                pvc.acriss_fuel = result.acriss_fuel
                pvc.classification_confidence = result.confidence
                pvc.pending_review = True
                return

            pvc.acriss_category = None
            pvc.acriss_body_type = None
            pvc.acriss_transmission = None
            pvc.acriss_fuel = None
            pvc.classification_confidence = result.confidence
            pvc.pending_review = True
            return

        pvc.acriss_category = result.acriss_category
        pvc.acriss_body_type = result.acriss_body_type
        pvc.acriss_transmission = result.acriss_transmission
        pvc.acriss_fuel = result.acriss_fuel
        pvc.classification_confidence = result.confidence
        pvc.pending_review = False
