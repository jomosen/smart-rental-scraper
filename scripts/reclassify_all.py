"""Reclassify ALL active provider_vehicle_categories via Gemini.

Processes active PVCs for all active providers (or one if --provider is
given). Inactive PVCs are intentionally skipped: they are invisible in the
pricing view and reclassifying them wastes API calls without benefit.

IMPORTANT: run scripts/seed_acriss_codes.py BEFORE this script so that all
new ACRISS codes exist in acriss_codes. The acriss_code column in
provider_vehicle_categories is a DB-computed FK — if the LLM returns a code
that does not yet exist in acriss_codes the commit will fail.

Usage:
    python scripts/reclassify_all.py [--provider SCRAPER_KEY] [--dry-run]

Per-PVC output format (one line per vehicle):
    id=<N>  <example_models[:42]>  <OLD> → <NEW>  conf=<X.XX>  pending=<bool>

Exit codes:
    0 — completed (some PVCs may still be pending_review — expected)
    1 — fatal error (DB unreachable, no providers found, etc.)
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from src.saas.application.classification.acriss_loader import load_acriss_specs
from src.saas.application.classification.dtos import (
    ClassificationResult,
    VehicleClassificationInput,
)
from src.saas.infrastructure.classification.gemini_service import GeminiClassificationService
from src.saas.infrastructure.persistence.engine import super_engine
from src.saas.infrastructure.persistence.models.catalog import (
    Provider,
    ProviderVehicleCategory,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_YAML_PATH = Path(__file__).resolve().parent.parent / "acriss_codes.yaml"
_REVIEWED_PATH = Path(__file__).resolve().parent.parent / "acriss_reviewed.yaml"


def _load_reviewed() -> set[tuple[str, str, str]]:
    """Load accepted (provider, external_code, acriss_code) triples.

    A PVC the LLM re-flags as pending_review is auto-cleared only when its
    resulting code still matches the accepted one — a changed code stays flagged.
    """
    if not _REVIEWED_PATH.exists():
        return set()
    data = yaml.safe_load(_REVIEWED_PATH.read_text(encoding="utf-8")) or {}
    out: set[tuple[str, str, str]] = set()
    for e in data.get("reviewed", []) or []:
        out.add((e["provider"], e["external_code"], e["acriss_code"]))
    return out


def _old_code(pvc: ProviderVehicleCategory) -> str:
    """Current 4-char code, or '????' if any attribute is unclassified."""
    parts = [pvc.acriss_category, pvc.acriss_body_type, pvc.acriss_transmission, pvc.acriss_fuel]
    if any(p is None for p in parts):
        return "????"
    return "".join(parts)


def _new_code(result: ClassificationResult) -> str:
    """Result 4-char code, or 'null' if unclassifiable."""
    if result.acriss_category is None:
        return "null"
    return (
        (result.acriss_category or "")
        + (result.acriss_body_type or "")
        + (result.acriss_transmission or "")
        + (result.acriss_fuel or "")
    )


def _apply_result(pvc: ProviderVehicleCategory, result: ClassificationResult) -> None:
    pvc.acriss_category = result.acriss_category
    pvc.acriss_body_type = result.acriss_body_type
    pvc.acriss_transmission = result.acriss_transmission
    pvc.acriss_fuel = result.acriss_fuel
    pvc.classification_confidence = result.confidence
    pvc.pending_review = result.pending_review


def reclassify_all(provider_filter: str | None = None, dry_run: bool = False) -> int:
    """Reclassify all active PVCs. Returns total PVC count processed (>= 0), or -1 on fatal error."""
    acriss_specs = load_acriss_specs(_YAML_PATH)
    service = GeminiClassificationService(acriss_types=acriss_specs)
    reviewed = _load_reviewed()

    engine = super_engine()
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    total_processed = 0
    total_changed = 0
    total_pending = 0
    total_autocleared = 0
    total_null = 0
    providers_failed: list[str] = []

    with Session() as session:
        provider_query = select(Provider).where(Provider.status == "active")
        if provider_filter:
            provider_query = provider_query.where(Provider.scraper_key == provider_filter)
        providers = list(session.scalars(provider_query).all())

        if not providers:
            logger.error(
                "No active providers found%s.",
                f" matching scraper_key='{provider_filter}'" if provider_filter else "",
            )
            return -1

        logger.info(
            "Found %d active provider(s): %s",
            len(providers),
            [p.scraper_key for p in providers],
        )

        for provider in providers:
            pvcs = list(session.scalars(
                select(ProviderVehicleCategory).where(
                    ProviderVehicleCategory.provider_id == provider.id,
                    ProviderVehicleCategory.active == True,  # noqa: E712
                ).order_by(ProviderVehicleCategory.id)
            ).all())

            if not pvcs:
                logger.info("  [%s] no active PVCs — skip.", provider.scraper_key)
                continue

            logger.info(
                "  [%s] reclassifying %d active PVC(s) ...",
                provider.scraper_key,
                len(pvcs),
            )

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
                for pvc in pvcs
            ]

            try:
                results = service.classify_provider_batch(provider.scraper_key, vehicles)
            except Exception as exc:
                logger.error(
                    "  [%s] Gemini call failed: %s — provider skipped.",
                    provider.scraper_key,
                    exc,
                )
                providers_failed.append(provider.scraper_key)
                continue

            provider_changed = 0
            for pvc, result in zip(pvcs, results):
                old = _old_code(pvc)
                new = _new_code(result)
                changed = old != new
                models_short = (pvc.example_models or "")[:42]

                logger.info(
                    "    id=%-6d  %-44s  %s → %s  conf=%.2f  pending=%s%s",
                    pvc.id,
                    models_short,
                    old,
                    new,
                    result.confidence,
                    result.pending_review,
                    "  *" if changed else "",
                )
                # Print rationale when there's uncertainty or the classification changed
                if result.rationale and (result.pending_review or result.confidence < 0.85 or changed):
                    logger.info("      reason: %s", result.rationale)

                if result.acriss_category is None:
                    total_null += 1
                if result.pending_review:
                    total_pending += 1
                if changed:
                    provider_changed += 1
                    total_changed += 1

                autocleared = (
                    result.pending_review
                    and (provider.scraper_key, pvc.external_code, new) in reviewed
                )
                if autocleared:
                    total_autocleared += 1
                    logger.info(
                        "      %s pending_review (in acriss_reviewed.yaml)",
                        "WOULD auto-clear" if dry_run else "auto-cleared",
                    )

                if not dry_run:
                    _apply_result(pvc, result)
                    if autocleared:
                        pvc.pending_review = False

            total_processed += len(pvcs)

            if dry_run:
                logger.info(
                    "  [%s] DRY RUN — %d would change, nothing staged.",
                    provider.scraper_key,
                    provider_changed,
                )
            else:
                try:
                    session.commit()
                    logger.info(
                        "  [%s] committed %d PVC(s), %d changed.",
                        provider.scraper_key,
                        len(pvcs),
                        provider_changed,
                    )
                except Exception as exc:
                    session.rollback()
                    logger.error(
                        "  [%s] commit failed: %s — rolled back (provider skipped).",
                        provider.scraper_key,
                        exc,
                    )
                    providers_failed.append(provider.scraper_key)

    dry_tag = "  [DRY RUN — nothing committed]" if dry_run else ""
    logger.info(
        "=== SUMMARY === processed=%d  changed=%d  pending_review=%d (auto-cleared=%d)  unclassifiable=%d%s",
        total_processed,
        total_changed,
        total_pending,
        total_autocleared,
        total_null,
        dry_tag,
    )
    if providers_failed:
        logger.warning("Providers with errors (need manual retry): %s", providers_failed)

    return total_processed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reclassify all active PVCs via Gemini (batch by provider)."
    )
    parser.add_argument(
        "--provider",
        metavar="SCRAPER_KEY",
        default=None,
        help="Limit to one provider by scraper_key (default: all active providers)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log results without committing to the database",
    )
    args = parser.parse_args()

    n = reclassify_all(provider_filter=args.provider, dry_run=args.dry_run)
    return 0 if n >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
