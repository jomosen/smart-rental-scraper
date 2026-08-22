"""
Fase D1 — execution step.

Reads the active recipe from the database and runs it with ZERO LLM calls.
Optionally validates the output against a fresh LLM scrape (--validate).

Replaces the old YAML-based approach from C1.

Usage:
    python run_run_recipe.py [--visible] [--location CITY]
                             [--pickup-offset DAYS] [--pickup-time HH:MM]
                             [--dropoff-time HH:MM] [--provider-key KEY]
                             [--validate]

Prerequisites:
  - DB running with a recipe saved (run_build_recipe.py first).
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
from src.scraper.infrastructure.builder.extraction.extraction_verifier import verify
from src.scraper.infrastructure.builder.location_explorer import create_log_dir
from src.scraper.infrastructure.builder.recipe_executor import run_recipe as _run_recipe_browser
from src.scraper.infrastructure.builder.scraper_engine import ScrapeResult, scrape

# src/scraper application + persistence
from src.scraper.application.builder.run_recipe import run_recipe_from_db
from src.scraper.domain.builder.recipe_health import RecipeHealthCheck, check_recipe_health
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
        description="Execute a recipe from DB without LLM and optionally validate (Fase D1)"
    )
    p.add_argument("--visible", action="store_true", help="Show browser window")
    p.add_argument("--location", default="Alicante")
    p.add_argument("--pickup-offset", type=int, default=48, metavar="DAYS")
    p.add_argument("--pickup-time", default="10:00")
    p.add_argument("--dropoff-time", default="10:00")
    p.add_argument("--provider-key", default="centauro",
                   help="providers.code to look up recipe for (default: centauro)")
    p.add_argument("--validate", action="store_true",
                   help="Also run scrape() with LLM and compare outputs")
    return p.parse_args()


def _get_provider_id(session: Session, provider_key: str) -> int:
    row = session.scalar(select(Provider).where(Provider.code == provider_key))
    if row is None:
        raise ValueError(
            f"Provider '{provider_key}' not found in providers table."
        )
    return row.id


def print_recipe_report(
    result: ScrapeResult,
    label: str = "recipe",
    check_llm_calls: bool = True,
) -> None:
    llm_flag = (
        "" if not check_llm_calls or result.llm_calls == 0
        else "  [BUG: llm_calls > 0]"
    )
    overall = "[OK]" if result.success else "[FAIL]"
    print(f"  {overall}  [{label}]  "
          f"duration={result.duration_seconds:.1f}s  "
          f"cost={result.cost_estimate_eur:.4f} EUR  "
          f"llm_calls={result.llm_calls}{llm_flag}")

    if result.failed_phase:
        print(f"  failed_phase:    {result.failed_phase}")

    extracted = result.vehicles or list(result.dom_vehicles or [])
    print(f"  vehicles:        {len(extracted)}")
    print(f"  scroll:          rounds={result.scroll_rounds}  "
          f"final_count={result.scroll_final_count}")

    sample = extracted[:3]
    if sample:
        print(f"  sample (first 3):")
        for v in sample:
            price_str = f"{v.price_final} {v.currency}" if v.price_final else "?"
            seats_str = f"  {v.seats}p" if v.seats else ""
            print(f"    {v.model or '?':<25}  {(v.group_code or '?'):<8}  "
                  f"{(v.transmission or '?'):<3}  {price_str}{seats_str}")

    if result.error:
        print(f"  error:           {result.error}")


def print_health_report(health: RecipeHealthCheck, provider_key: str) -> None:
    status = "[OK] healthy" if health.healthy else "[BROKEN]"
    print(f"  recipe health:   {status}")
    print(f"    vehicles:        {health.vehicle_count}")
    if health.vehicle_count > 0:
        print(f"    with model:      {health.pct_with_model:.0%}")
        print(f"    with price:      {health.pct_with_price:.0%}")
        print(f"    with key fields: {health.pct_with_key_fields:.0%}")
        print(f"    prices in range: {'yes' if health.prices_in_range else 'NO'}")
    if health.failed_checks:
        print("    failed:")
        for fc in health.failed_checks:
            print(f"      [{fc}]")
        print(f"    -> Recipe likely stale. Regenerate: "
              f"python run_build_recipe.py --provider-key {provider_key}")
    if health.warnings:
        for w in health.warnings:
            print(f"    warning: {w}")


async def main() -> int:
    args = parse_args()
    targets = compute_targets(args)
    provider_key = args.provider_key

    engine = app_engine()
    factory = make_session_factory(engine)

    session = factory()
    try:
        provider_id = _get_provider_id(session, provider_key)
        repo = ProviderRecipeRepository(session)
        recipe = repo.get_active_recipe(provider_id)
    finally:
        session.close()

    if recipe is None:
        print(f"No active recipe for '{provider_key}' in DB.")
        print(f"Run: python run_build_recipe.py --provider-key {provider_key}")
        return 1

    print(f"\n=== recipe: {provider_key} | url={recipe.url} ===")
    print(f"  Discovered: {recipe.discovered_at}")
    print(f"  card_source: {recipe.card_source}  "
          f"cookies_strategy: {recipe.cookies_strategy}")

    # ── Recipe run (no LLM) ───────────────────────────────────────────────────
    log_dir = create_log_dir(provider_key, suffix="_run_recipe")
    print(f"  Logs: {log_dir}")

    session = factory()
    try:
        repo = ProviderRecipeRepository(session)
        recipe_result = await run_recipe_from_db(
            provider_id=provider_id,
            targets=targets,
            log_dir=log_dir,
            headless=not args.visible,
            repo=repo,
            executor=_run_recipe_browser,
        )
    finally:
        session.close()

    print_recipe_report(recipe_result, label="recipe/no-llm")

    # ── Health check (no LLM) ─────────────────────────────────────────────────
    health = check_recipe_health(recipe_result)
    print_health_report(health, provider_key)

    # ── Optional LLM validation ───────────────────────────────────────────────
    if args.validate:
        print("\n  -- LLM validation --")

        url = next(
            (u for n, u in TEST_CASES if n == provider_key),
            TEST_CASES[0][1],
        )
        log_dir_llm = create_log_dir(provider_key, suffix="_validate_llm")
        print(f"  Logs (LLM): {log_dir_llm}")

        llm_result = await scrape(
            url, targets, log_dir_llm, headless=not args.visible
        )
        print_recipe_report(llm_result, label="scrape/llm", check_llm_calls=False)

        if llm_result.vehicles and recipe_result.vehicles:
            verification = verify(llm_result.vehicles, recipe_result.vehicles)
            match_flag = "[OK]" if verification.match else "[FAIL]"
            print(f"\n  validation:      {match_flag}  {verification.rationale}")
            print(f"  llm_count={verification.llm_count}  "
                  f"recipe_count={verification.dom_count}")
            compared = {
                f: pct for f, pct in verification.field_agreement.items()
                if pct < 1.0 or f in ("model", "group_code", "price_final")
            }
            if compared:
                print("  field_agreement:")
                for field, pct in compared.items():
                    bar = "[OK]" if pct >= 0.90 else "[LOW]"
                    print(f"    {field:<22}  {bar}  {pct:.0%}")
            if verification.mismatches:
                print(f"  mismatches ({len(verification.mismatches)}):")
                for m in verification.mismatches[:5]:
                    print(f"    - {m}")
                if len(verification.mismatches) > 5:
                    print(f"    ... and {len(verification.mismatches) - 5} more")
        else:
            if not llm_result.vehicles:
                print("  validation skipped: LLM returned no vehicles")
            if not recipe_result.vehicles:
                print("  validation skipped: recipe returned no vehicles")

    return 0 if health.healthy else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
