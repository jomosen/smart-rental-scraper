"""Apply taxonomy.yaml to the database.

Idempotent: re-running on an unchanged YAML produces zero changes.

The script does two things:

  1. Synchronise canonical_vehicle_types with categories in the YAML:
     insert new ones, update changed name/description, deprecate
     missing ones (mark active=false, never delete).

  2. Apply initial_classification to provider_vehicle_categories that
     match (provider_code, external_code). Only sets canonical_type_id
     when the PVC currently has NULL — never overwrites an existing
     classification.

Usage:
    python scripts/seed_taxonomy.py [--dry-run] [--yaml PATH] [--verbose]

Exit codes:
    0 — success (changes applied or no changes needed)
    1 — error (YAML invalid, DB unreachable, integrity violation)
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.saas.infrastructure.persistence.engine import super_engine
from src.saas.infrastructure.persistence.models.catalog import (
    CanonicalVehicleType,
    Provider,
    ProviderVehicleCategory,
)
from src.saas.infrastructure.persistence.repositories import CanonicalVehicleTypeRepository

from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

_DEFAULT_YAML = Path(__file__).resolve().parent.parent / "taxonomy.yaml"


class SeedError(Exception):
    """Raised when a fatal validation or integrity error prevents seeding."""


@dataclass
class SeedStats:
    inserted: int = 0
    updated: int = 0
    deprecated: int = 0
    classified: int = 0


def _validate_yaml(data: dict) -> None:
    """Validate YAML structure. Raises SeedError on invalid input.

    Checks are purely against the dict — no DB access.
    """
    if not isinstance(data.get("taxonomy_version"), int):
        raise SeedError("YAML must have 'taxonomy_version' as an integer")
    if not isinstance(data.get("categories"), list):
        raise SeedError("YAML must have 'categories' as a list")

    known_codes: set[str] = set()
    for cat in data["categories"]:
        for field_name in ("code", "name", "description"):
            if not isinstance(cat.get(field_name), str):
                raise SeedError(
                    f"Category missing required string field '{field_name}': {cat}"
                )
        known_codes.add(cat["code"])

    for entry in data.get("initial_classification", []):
        ct_code = entry.get("canonical_type_code")
        if ct_code not in known_codes:
            raise SeedError(
                f"Initial classification references unknown canonical_type '{ct_code}' "
                "— fix YAML (not present in categories list)"
            )


def seed_taxonomy(
    session,
    taxonomy_data: dict,
    dry_run: bool = False,
) -> SeedStats:
    """Apply taxonomy to the DB using the provided session.

    Does NOT commit — the caller owns the transaction.
    Raises SeedError on fatal validation or integrity errors.
    """
    _validate_yaml(taxonomy_data)

    version: int = taxonomy_data["taxonomy_version"]
    categories: list[dict] = taxonomy_data["categories"]
    initial_classification: list[dict] = taxonomy_data.get("initial_classification", [])

    stats = SeedStats()
    repo = CanonicalVehicleTypeRepository(session)
    yaml_codes = {cat["code"] for cat in categories}

    # ── Step 1: Synchronise canonical_vehicle_types ───────────────────────────

    for cat in categories:
        code = cat["code"]
        family = str(cat.get("family", "")).strip()
        name = cat["name"].strip()
        description = str(cat["description"]).strip()

        existing = repo.get_by_code(code)
        if existing is None:
            if dry_run:
                logger.info("WOULD insert canonical type: %s", code)
            else:
                repo.upsert(code, name, description, version, family)
                logger.info("Inserted canonical type: %s", code)
            stats.inserted += 1
        elif (
            existing.name != name
            or existing.description != description
            or existing.family != family
        ):
            if dry_run:
                logger.info("WOULD update canonical type: %s", code)
            else:
                repo.upsert(code, name, description, version, family)
                logger.info("Updated canonical type: %s", code)
            stats.updated += 1
        # else: fields unchanged — no-op, no log

    for db_type in repo.get_all_active():
        if db_type.code not in yaml_codes:
            if dry_run:
                logger.info("WOULD deprecate canonical type: %s", db_type.code)
            else:
                repo.mark_deprecated(db_type.code)
                logger.info("Deprecated canonical type: %s", db_type.code)
            stats.deprecated += 1

    # ── Step 2: Apply initial_classification ──────────────────────────────────

    for entry in initial_classification:
        provider_code: str = entry["provider_code"]
        external_code: str = str(entry["external_code"])
        ct_code: str = entry["canonical_type_code"]
        confidence: float | None = entry.get("confidence")

        provider = session.scalar(
            select(Provider).where(Provider.code == provider_code)
        )
        if provider is None:
            logger.warning(
                "Provider '%s' not found in database — skipping classification "
                "for external_code='%s'",
                provider_code,
                external_code,
            )
            continue

        ct = repo.get_by_code(ct_code)
        if ct is None:
            raise SeedError(
                f"Internal error: canonical_type '{ct_code}' not found after sync. "
                "This should not happen — check DB state."
            )

        pvcs = list(
            session.scalars(
                select(ProviderVehicleCategory).where(
                    ProviderVehicleCategory.provider_id == provider.id,
                    ProviderVehicleCategory.external_code == external_code,
                )
            ).all()
        )

        for pvc in pvcs:
            if pvc.canonical_type_id is None:
                if dry_run:
                    logger.info(
                        "WOULD classify PVC id=%d (provider=%s, external_code='%s') → %s",
                        pvc.id,
                        provider_code,
                        external_code,
                        ct_code,
                    )
                else:
                    pvc.canonical_type_id = ct.id
                    pvc.classification_confidence = confidence
                    pvc.classification_taxonomy_version = version
                    logger.info(
                        "Classified PVC id=%d (provider=%s, external_code='%s') → %s",
                        pvc.id,
                        provider_code,
                        external_code,
                        ct_code,
                    )
                stats.classified += 1
            else:
                logger.debug(
                    "PVC id=%d (provider=%s, external_code='%s') already classified — skipping",
                    pvc.id,
                    provider_code,
                    external_code,
                )

    if not dry_run:
        session.flush()

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply taxonomy.yaml to the database (idempotent)."
    )
    parser.add_argument(
        "--yaml",
        type=Path,
        default=_DEFAULT_YAML,
        metavar="PATH",
        help="Path to taxonomy YAML file (default: taxonomy.yaml in repo root)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log intended changes without applying them to the database",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    try:
        with open(args.yaml, encoding="utf-8") as f:
            taxonomy_data = yaml.safe_load(f)
    except FileNotFoundError:
        logger.error("YAML file not found: %s", args.yaml)
        return 1
    except yaml.YAMLError as exc:
        logger.error("YAML parse error: %s", exc)
        return 1

    try:
        _validate_yaml(taxonomy_data)
    except SeedError as exc:
        logger.error("YAML validation error: %s", exc)
        return 1

    try:
        engine = super_engine()
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 1

    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = factory()
    try:
        stats = seed_taxonomy(session, taxonomy_data, dry_run=args.dry_run)
        if args.dry_run:
            session.rollback()
            logger.info(
                "DRY RUN — no changes committed. "
                "Would insert %d, update %d, deprecate %d, classify %d.",
                stats.inserted,
                stats.updated,
                stats.deprecated,
                stats.classified,
            )
        else:
            session.commit()
            logger.info(
                "Seed completed. Inserted: %d, Updated: %d, Deprecated: %d, "
                "Classifications applied: %d.",
                stats.inserted,
                stats.updated,
                stats.deprecated,
                stats.classified,
            )
    except SeedError as exc:
        session.rollback()
        logger.error("Seed failed: %s", exc)
        return 1
    except Exception as exc:
        session.rollback()
        logger.error("Unexpected error during seed: %s", exc)
        raise
    finally:
        session.close()
        engine.dispose()

    return 0


if __name__ == "__main__":
    sys.exit(main())
