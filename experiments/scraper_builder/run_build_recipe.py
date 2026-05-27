"""
Fase D1 — discovery step.

Runs scrape() (with LLM, ~€0.24) once to discover the site, then saves the
recipe to the database (provider_recipes table).  Replaces the old YAML file
approach from C1.

Usage:
    python run_build_recipe.py [--visible] [--location CITY]
                               [--pickup-offset DAYS] [--pickup-time HH:MM]
                               [--dropoff-time HH:MM] [--provider-key KEY]

Prerequisites:
  - DB running with migrations applied (alembic upgrade head).
  - providers table has a row with code == --provider-key.
  - ADMIN_DATABASE_URL or APP_DATABASE_URL set in .env.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

# ── Make src/ importable from experiments/ context ───────────────────────────
_PROJECT_ROOT = Path(__file__).parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv()

# experiments/ infrastructure (still lives here in D1)
from src.scraper.infrastructure.builder.location_explorer import create_log_dir
from src.scraper.infrastructure.builder.scraper_engine import scrape

# src/scraper application + persistence
from src.scraper.application.builder.build_recipe import build_recipe
from src.scraper.infrastructure.repositories.provider_recipe_repository import (
    ProviderRecipeRepository,
)
from src.saas.infrastructure.persistence.engine import app_engine
from src.saas.infrastructure.persistence.session import make_session_factory
from src.saas.infrastructure.persistence.models.catalog import Provider

from sqlalchemy.orm import Session
from sqlalchemy import select

TEST_CASES = [
    ("centauro", "https://www.centauro.net"),
]


def compute_targets(args: argparse.Namespace) -> dict:
    pickup_d = date.today() + timedelta(days=args.pickup_offset)
    return_d = pickup_d + timedelta(days=7)
    return {
        "location": args.location,
        "pickup_date": pickup_d,
        "return_date": return_d,
        "pickup_time": args.pickup_time,
        "return_time": args.dropoff_time,
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Discover a site with LLM and save a recipe to DB (Fase D1)"
    )
    p.add_argument("--visible", action="store_true", help="Show browser window")
    p.add_argument("--location", default="Alicante")
    p.add_argument("--pickup-offset", type=int, default=48, metavar="DAYS")
    p.add_argument("--pickup-time", default="10:00")
    p.add_argument("--dropoff-time", default="10:00")
    p.add_argument("--provider-key", default=None,
                   help="Recipe key matching providers.code (default: site name)")
    return p.parse_args()


def _get_provider_id(session: Session, provider_key: str) -> int:
    row = session.scalar(select(Provider).where(Provider.code == provider_key))
    if row is None:
        raise ValueError(
            f"Provider '{provider_key}' not found in providers table. "
            "Add it via CatalogSyncService or insert manually first."
        )
    return row.id


async def main() -> None:
    args = parse_args()
    targets = compute_targets(args)

    engine = app_engine()
    factory = make_session_factory(engine)

    for name, url in TEST_CASES:
        provider_key = args.provider_key or name
        log_dir = create_log_dir(name, suffix="_build_recipe")
        print(f"\n=== {name} | {provider_key} -> {targets} ===")
        print(f"  Logs: {log_dir}")

        result = await scrape(url, targets, log_dir, headless=not args.visible)

        print(f"  {'[OK]' if result.success else '[FAIL]'}  "
              f"duration={result.duration_seconds:.1f}s  "
              f"cost={result.cost_estimate_eur:.4f} EUR  "
              f"llm_calls={result.llm_calls}  "
              f"vehicles={len(result.vehicles)}")

        if not result.success:
            print(f"  Discovery failed at phase={result.failed_phase!r}")
            print(f"  error: {result.error}")
            continue

        session = factory()
        try:
            provider_id = _get_provider_id(session, provider_key)
            repo = ProviderRecipeRepository(session)
            recipe = build_recipe(provider_key, result, log_dir, repo, provider_id)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        print(f"  Recipe saved to DB (provider_id={provider_id})")
        print(f"  form_fields:      {list(recipe.form_fields)}")
        print(f"  field_extractors: {[e.field for e in recipe.field_extractors]}")
        print(f"  card_source:      {recipe.card_source}")


if __name__ == "__main__":
    asyncio.run(main())
