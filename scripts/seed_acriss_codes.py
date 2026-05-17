"""Apply acriss_codes.yaml to the database.

Idempotent: re-running on an unchanged YAML produces zero changes.

The script synchronises the acriss_codes table with the YAML:
  - Insert new codes.
  - Update changed display_name / description / criteria / examples.
  - Deactivate codes present in the DB but absent from the YAML
    (never deletes — deactivated rows preserve historical FK references).

Usage:
    python scripts/seed_acriss_codes.py [--dry-run] [--yaml PATH] [--verbose]

Exit codes:
    0 — success (changes applied or no changes needed)
    1 — error (YAML invalid, DB unreachable, integrity violation)
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from src.saas.infrastructure.persistence.engine import super_engine
from src.saas.infrastructure.persistence.repositories import AcrissCodeRepository

logger = logging.getLogger(__name__)

_DEFAULT_YAML = Path(__file__).resolve().parent.parent / "acriss_codes.yaml"

_VALID_CODE_LEN = 4


class SeedError(Exception):
    """Raised when a fatal validation or integrity error prevents seeding."""


@dataclass
class SeedStats:
    inserted: int = 0
    updated: int = 0
    deactivated: int = 0


def _validate_yaml(data: dict) -> None:
    """Validate YAML structure. Raises SeedError on invalid input."""
    if not isinstance(data.get("acriss_codes"), list):
        raise SeedError("YAML must have 'acriss_codes' as a list")

    seen_codes: set[str] = set()
    for entry in data["acriss_codes"]:
        for field_name in ("code", "display_name", "description"):
            if not isinstance(entry.get(field_name), str):
                raise SeedError(
                    f"Entry missing required string field '{field_name}': {entry}"
                )
        code = entry["code"]
        if len(code) != _VALID_CODE_LEN:
            raise SeedError(
                f"ACRISS code must be exactly {_VALID_CODE_LEN} characters, got: {code!r}"
            )
        if code in seen_codes:
            raise SeedError(f"Duplicate ACRISS code in YAML: {code!r}")
        seen_codes.add(code)

        for list_field in ("criteria", "examples"):
            val = entry.get(list_field, [])
            if not isinstance(val, list):
                raise SeedError(
                    f"Field '{list_field}' must be a list in entry '{code}'"
                )


def seed_acriss_codes(
    session,
    data: dict,
    dry_run: bool = False,
) -> SeedStats:
    """Apply ACRISS codes to the DB using the provided session.

    Does NOT commit — the caller owns the transaction.
    Raises SeedError on fatal validation or integrity errors.
    """
    _validate_yaml(data)

    entries: list[dict] = data["acriss_codes"]
    stats = SeedStats()
    repo = AcrissCodeRepository(session)
    yaml_codes: set[str] = set()

    for entry in entries:
        code: str = entry["code"]
        display_name: str = entry["display_name"].strip()
        description: str = str(entry["description"]).strip()
        criteria: list[str] = entry.get("criteria", [])
        examples: list[str] = entry.get("examples", [])
        yaml_codes.add(code)

        existing = repo.get_by_code(code)
        if existing is None:
            if dry_run:
                logger.info("WOULD insert ACRISS code: %s (%s)", code, display_name)
            else:
                repo.upsert(code, display_name, description, criteria, examples)
                logger.info("Inserted ACRISS code: %s (%s)", code, display_name)
            stats.inserted += 1
        elif (
            existing.display_name != display_name
            or existing.description != description
            or existing.criteria != criteria
            or existing.examples != examples
            or not existing.active
        ):
            if dry_run:
                logger.info("WOULD update ACRISS code: %s", code)
            else:
                repo.upsert(code, display_name, description, criteria, examples, active=True)
                logger.info("Updated ACRISS code: %s", code)
            stats.updated += 1

    if dry_run:
        active_in_db = repo.list_active()
        for row in active_in_db:
            if row.code not in yaml_codes:
                logger.info("WOULD deactivate ACRISS code: %s", row.code)
                stats.deactivated += 1
    else:
        stats.deactivated = repo.deactivate_missing(yaml_codes)
        if stats.deactivated:
            logger.info("Deactivated %d ACRISS code(s) not in YAML", stats.deactivated)

    if not dry_run:
        session.flush()

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply acriss_codes.yaml to the database (idempotent)."
    )
    parser.add_argument(
        "--yaml",
        type=Path,
        default=_DEFAULT_YAML,
        metavar="PATH",
        help="Path to ACRISS codes YAML file (default: acriss_codes.yaml in repo root)",
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
            data = yaml.safe_load(f)
    except FileNotFoundError:
        logger.error("YAML file not found: %s", args.yaml)
        return 1
    except yaml.YAMLError as exc:
        logger.error("YAML parse error: %s", exc)
        return 1

    try:
        _validate_yaml(data)
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
        stats = seed_acriss_codes(session, data, dry_run=args.dry_run)
        if args.dry_run:
            session.rollback()
            logger.info(
                "DRY RUN — no changes committed. "
                "Would insert %d, update %d, deactivate %d.",
                stats.inserted,
                stats.updated,
                stats.deactivated,
            )
        else:
            session.commit()
            logger.info(
                "Seed completed. Inserted: %d, Updated: %d, Deactivated: %d.",
                stats.inserted,
                stats.updated,
                stats.deactivated,
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
