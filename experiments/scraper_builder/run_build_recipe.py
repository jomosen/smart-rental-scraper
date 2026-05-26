"""
Fase C1 — discovery step.

Runs scrape() (with LLM, ~€0.24) once to discover the site, then writes the
result as a recipe YAML that run_run_recipe.py can replay without any LLM.

Usage:
    python run_build_recipe.py [--visible] [--location CITY]
                               [--pickup-offset DAYS] [--pickup-time HH:MM]
                               [--dropoff-time HH:MM] [--provider-key KEY]
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from location_explorer import create_log_dir
from recipe import build_recipe, write_recipe
from scraper_engine import scrape

_BASE = Path(__file__).parent
RECIPES_DIR = _BASE / "recipes"

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
        description="Discover a site with LLM and write a recipe YAML (Fase C1)"
    )
    p.add_argument("--visible", action="store_true", help="Show browser window")
    p.add_argument("--location", default="Alicante")
    p.add_argument("--pickup-offset", type=int, default=48, metavar="DAYS")
    p.add_argument("--pickup-time", default="10:00")
    p.add_argument("--dropoff-time", default="10:00")
    p.add_argument("--provider-key", default=None,
                   help="Recipe key (default: site name from TEST_CASES)")
    return p.parse_args()


async def main() -> None:
    args = parse_args()
    targets = compute_targets(args)

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

        recipe = build_recipe(provider_key, result, log_dir)
        recipe_path = RECIPES_DIR / f"{provider_key}.yaml"
        write_recipe(recipe, recipe_path)

        print(f"  Recipe written: {recipe_path}")
        print(f"  form_fields:    {list(recipe.form_fields)}")
        print(f"  field_extractors: {[e.field for e in recipe.field_extractors]}")
        print(f"  card_source:    {recipe.card_source}")


if __name__ == "__main__":
    asyncio.run(main())
