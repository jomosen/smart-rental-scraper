from __future__ import annotations

import datetime
import hashlib
import logging
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.catalog import CanonicalVehicleType, ProviderVehicleCategory
from ....application.classification.dtos import ClassificationResult

logger = logging.getLogger(__name__)


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
        """Return all active provider_vehicle_categories for this tuple."""
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
        transmission: Optional[str],
        fuel_type: Optional[str],
        classification: ClassificationResult,
    ) -> ProviderVehicleCategory:
        """Find or create a PVC, apply the given classification, and update attributes.

        Identity:
          - external_code is not None → unique by (provider, location, rate, external_code)
          - external_code is None     → unique by (provider, location, rate, attributes_hash)

        Classification is always applied — the caller is responsible for
        deciding when to call the LLM (during the probe phase via
        SmartScraperOrchestrator._classify_probe_catalog).
        """
        now = datetime.datetime.now(tz=datetime.timezone.utc)

        if external_code is not None:
            pvc = self.get_by_external_code(
                provider_id, provider_location_id, provider_rate_id, external_code
            )
            row_hash: Optional[str] = None
        else:
            row_hash = self._compute_hash(example_models, seats, luggage, transmission, fuel_type)
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
                fuel_type=fuel_type,
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
            pvc.transmission = transmission
            pvc.fuel_type = fuel_type
            if external_name is not None:
                pvc.external_name = external_name

        self._apply_classification(pvc, classification, is_new)
        self._s.flush()
        return pvc

    # ── Private ──────────────────────────────────────────────────────────────

    @staticmethod
    def _compute_hash(
        example_models: str,
        seats: Optional[int],
        luggage: Optional[int],
        transmission: Optional[str],
        fuel_type: Optional[str],
    ) -> str:
        """SHA256 truncated to 16 hex chars over stable vehicle attributes."""
        key = "|".join([
            example_models or "",
            str(seats) if seats is not None else "",
            str(luggage) if luggage is not None else "",
            transmission or "",
            fuel_type or "",
        ])
        return hashlib.sha256(key.encode()).hexdigest()[:16]

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
        """Write classification result onto the PVC row.

        Pending-review policy:
          - Existing PVC with a previous canonical_type_id: keep the cached
            classification, mark pending_review=True so the operator knows
            confidence dropped.
          - New PVC or PVC with no previous classification: store NULL
            canonical_type_id and pending_review=True.
        """
        if result.pending_review and not is_new and pvc.canonical_type_id is not None:
            pvc.classification_confidence = result.confidence
            pvc.pending_review = True
            return

        if result.canonical_type_code is None:
            pvc.canonical_type_id = None
            pvc.classification_confidence = result.confidence
            pvc.classification_taxonomy_version = result.taxonomy_version
            pvc.pending_review = True
            return

        canonical = self._s.scalar(
            select(CanonicalVehicleType).where(
                CanonicalVehicleType.code == result.canonical_type_code
            )
        )
        if canonical is None:
            logger.warning(
                "LLM returned canonical code '%s' which is not in the DB — "
                "marking pending_review",
                result.canonical_type_code,
            )
            pvc.canonical_type_id = None
            pvc.classification_confidence = result.confidence
            pvc.classification_taxonomy_version = result.taxonomy_version
            pvc.pending_review = True
        else:
            pvc.canonical_type_id = canonical.id
            pvc.classification_confidence = result.confidence
            pvc.classification_taxonomy_version = result.taxonomy_version
            pvc.pending_review = False
