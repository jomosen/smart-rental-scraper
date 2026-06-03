"""Targeted reclassification of specific provider_vehicle_categories.

Usage:
    python scripts/reclassify_groups.py --ids 10 72 22 24

Loads PVC rows by ID, builds VehicleClassificationInput from their stored
attributes, passes them through GeminiClassificationService (unchanged),
and writes the result directly back to the PVC row.

The direct write bypasses the "keep cached code if pending_review" guard in
upsert_seen — this is intentional: the whole point of this script is to
force-apply a fresh classification regardless of the previous one.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from src.saas.application.classification.acriss_loader import load_acriss_specs
from src.saas.application.classification.dtos import ClassificationResult, VehicleClassificationInput
from src.saas.infrastructure.classification.gemini_service import GeminiClassificationService
from src.saas.infrastructure.persistence.engine import super_engine
from src.saas.infrastructure.persistence.models.catalog import ProviderVehicleCategory

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_YAML_PATH = Path(__file__).resolve().parent.parent / "acriss_codes.yaml"


def _apply_result(pvc: ProviderVehicleCategory, result: ClassificationResult) -> None:
    """Write classification result directly onto the PVC — force-applies regardless of confidence."""
    pvc.acriss_category = result.acriss_category
    pvc.acriss_body_type = result.acriss_body_type
    pvc.acriss_transmission = result.acriss_transmission
    pvc.acriss_fuel = result.acriss_fuel
    pvc.classification_confidence = result.confidence
    pvc.pending_review = result.pending_review


def reclassify(ids: list[int], dry_run: bool = False) -> None:
    acriss_specs = load_acriss_specs(_YAML_PATH)
    service = GeminiClassificationService(acriss_types=acriss_specs)

    engine = super_engine()
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    with Session() as session:
        pvcs = list(session.scalars(
            select(ProviderVehicleCategory).where(
                ProviderVehicleCategory.id.in_(ids)
            )
        ).all())

        if not pvcs:
            logger.error("No PVC rows found for IDs: %s", ids)
            return

        found_ids = {pvc.id for pvc in pvcs}
        missing = set(ids) - found_ids
        if missing:
            logger.warning("IDs not found in DB: %s", sorted(missing))

        # Group by provider_id so classify_provider_batch gets the right provider_code.
        # All PVCs have a single provider each; group to minimise API calls.
        from collections import defaultdict
        by_provider: dict[int, list[ProviderVehicleCategory]] = defaultdict(list)
        for pvc in pvcs:
            by_provider[pvc.provider_id].append(pvc)

        # Resolve provider_id → provider_code
        from sqlalchemy import text
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT id, scraper_key FROM providers WHERE id = ANY(:ids)"
            ), {"ids": list(by_provider.keys())}).fetchall()
        id_to_code = {r.id: r.scraper_key for r in rows}

        for provider_id, group in by_provider.items():
            provider_code = id_to_code.get(provider_id, str(provider_id))
            vehicles = [
                VehicleClassificationInput(
                    external_code=pvc.external_code,
                    external_name=pvc.external_name,
                    example_models=pvc.example_models or "",
                    seats=pvc.seats,
                    luggage=pvc.luggage,
                    transmission=pvc.transmission,
                    fuel_type=None,
                    representative_price_7d=None,
                    representative_currency=None,
                )
                for pvc in group
            ]

            logger.info(
                "Classifying %d vehicle(s) for provider %s: %s",
                len(vehicles),
                provider_code,
                [v.example_models for v in vehicles],
            )

            results = service.classify_provider_batch(provider_code, vehicles)

            for pvc, result in zip(group, results):
                old_code = (
                    (pvc.acriss_category or "") + (pvc.acriss_body_type or "")
                    + (pvc.acriss_transmission or "") + (pvc.acriss_fuel or "")
                )
                new_code = (
                    (result.acriss_category or "null") + (result.acriss_body_type or "")
                    + (result.acriss_transmission or "") + (result.acriss_fuel or "")
                ) if result.acriss_category else "null"

                logger.info(
                    "  id=%d %-30s  %s → %s  conf=%.2f  pending=%s",
                    pvc.id, pvc.example_models, old_code, new_code,
                    result.confidence, result.pending_review,
                )
                if result.rationale:
                    logger.info("    reasoning: %s", result.rationale)

                if not dry_run:
                    _apply_result(pvc, result)

        if dry_run:
            logger.info("DRY RUN — no changes committed.")
            session.rollback()
        else:
            session.commit()
            logger.info("Committed %d PVC update(s).", len(pvcs))


def main() -> int:
    parser = argparse.ArgumentParser(description="Reclassify specific PVC rows via Gemini.")
    parser.add_argument("--ids", nargs="+", type=int, required=True, help="PVC IDs to reclassify")
    parser.add_argument("--dry-run", action="store_true", help="Log results without committing")
    args = parser.parse_args()
    reclassify(args.ids, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
