from __future__ import annotations

import datetime
import hashlib
import logging
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.catalog import CanonicalVehicleType, ProviderVehicleCategory
from ....application.classification.dtos import VehicleAttributes
from ....application.classification.service import ClassificationService

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
        classification_service: ClassificationService,
        taxonomy_version: int,
    ) -> ProviderVehicleCategory:
        """Find or create a PVC, classify if needed, and update observed attributes.

        Cache policy: if the row already has classification_taxonomy_version equal
        to taxonomy_version, the LLM is not called — attributes are updated in
        place and the existing classification is preserved.

        Always returns a ProviderVehicleCategory (never None).
        """
        now = datetime.datetime.now(tz=datetime.timezone.utc)

        # Step 1: locate existing row
        if external_code is not None:
            pvc = self.get_by_external_code(
                provider_id, provider_location_id, provider_rate_id, external_code
            )
        else:
            pvc = self._find_by_attributes(
                provider_id, provider_location_id, provider_rate_id,
                example_models, seats, luggage, transmission, fuel_type,
            )

        # Step 2: determine whether classification is needed
        is_new = pvc is None
        need_classification = is_new or (pvc.classification_taxonomy_version != taxonomy_version)

        # Step 3: create or update the row
        if is_new:
            pvc = ProviderVehicleCategory(
                provider_id=provider_id,
                provider_location_id=provider_location_id,
                provider_rate_id=provider_rate_id,
                external_code=external_code,
                external_name=external_name or "",  # NOT NULL in DB
                example_models=example_models,
                seats=seats,
                luggage=luggage,
                transmission=transmission,
                fuel_type=fuel_type,
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

        # Step 4: classify if version has changed or row is new
        if need_classification:
            attrs = VehicleAttributes(
                example_models=example_models,
                seats=seats,
                luggage=luggage,
                transmission=transmission,
                fuel_type=fuel_type,
                external_code=external_code,
                external_name=external_name,
            )
            result = classification_service.classify(attrs)
            self._apply_classification(pvc, result, is_new)

        # Flush so the DB-assigned id is available and subsequent queries in
        # the same session can see this row.
        self._s.flush()
        return pvc

    # ── Private ──────────────────────────────────────────────────────────────

    def _find_by_attributes(
        self,
        provider_id: int,
        provider_location_id: int,
        provider_rate_id: int,
        example_models: str,
        seats: Optional[int],
        luggage: Optional[int],
        transmission: Optional[str],
        fuel_type: Optional[str],
    ) -> Optional[ProviderVehicleCategory]:
        """Locate a PVC by vehicle attributes when the provider exposes no external_code."""
        return self._s.scalar(
            select(ProviderVehicleCategory).where(
                ProviderVehicleCategory.provider_id == provider_id,
                ProviderVehicleCategory.provider_location_id == provider_location_id,
                ProviderVehicleCategory.provider_rate_id == provider_rate_id,
                ProviderVehicleCategory.external_code.is_(None),
                ProviderVehicleCategory.example_models == example_models,
                ProviderVehicleCategory.seats == seats,
                ProviderVehicleCategory.luggage == luggage,
                ProviderVehicleCategory.transmission == transmission,
                ProviderVehicleCategory.fuel_type == fuel_type,
            )
        )

    @staticmethod
    def _attributes_hash(
        example_models: str,
        seats: Optional[int],
        luggage: Optional[int],
        transmission: Optional[str],
        fuel_type: Optional[str],
    ) -> str:
        """Deterministic 16-char hex hash of vehicle attributes.

        Not stored in the DB — used for logging and debugging when
        external_code is None.
        """
        key = "|".join([
            example_models or "",
            str(seats) if seats is not None else "",
            str(luggage) if luggage is not None else "",
            transmission or "",
            fuel_type or "",
        ])
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def _apply_classification(
        self,
        pvc: ProviderVehicleCategory,
        result,
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
            # Cached fallback: preserve previous canonical classification
            pvc.classification_confidence = result.confidence
            pvc.pending_review = True
            # canonical_type_id and classification_taxonomy_version stay
            return

        if result.canonical_type_code is None:
            pvc.canonical_type_id = None
            pvc.classification_confidence = result.confidence
            pvc.classification_taxonomy_version = result.taxonomy_version
            pvc.pending_review = True
            return

        # Successful classification: look up the canonical row
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
