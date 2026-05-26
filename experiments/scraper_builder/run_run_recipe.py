"""
Fase C1 — execution step.

Loads a recipe YAML and runs it with ZERO LLM calls.  Optionally validates
the output against a fresh LLM scrape (--validate) using the existing verifier.

Usage:
    python run_run_recipe.py [--visible] [--location CITY]
                             [--pickup-offset DAYS] [--pickup-time HH:MM]
                             [--dropoff-time HH:MM] [--provider-key KEY]
                             [--validate]
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from extraction.extraction_verifier import verify
from location_explorer import create_log_dir
from recipe import load_recipe, run_recipe
from scraper_engine import ScrapeResult, scrape

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
        description="Execute a recipe without LLM and optionally validate (Fase C1)"
    )
    p.add_argument("--visible", action="store_true", help="Show browser window")
    p.add_argument("--location", default="Alicante")
    p.add_argument("--pickup-offset", type=int, default=48, metavar="DAYS")
    p.add_argument("--pickup-time", default="10:00")
    p.add_argument("--dropoff-time", default="10:00")
    p.add_argument("--provider-key", default="centauro",
                   help="Recipe key (default: centauro)")
    p.add_argument("--validate", action="store_true",
                   help="Also run scrape() with LLM and compare outputs")
    return p.parse_args()


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

    print(f"  vehicles:        {len(result.vehicles)}")
    print(f"  scroll:          rounds={result.scroll_rounds}  "
          f"final_count={result.scroll_final_count}")

    sample = result.vehicles[:3]
    if sample:
        print(f"  sample (first 3):")
        for v in sample:
            price_str = f"{v.price_final} {v.currency}" if v.price_final else "?"
            seats_str = f"  {v.seats}p" if v.seats else ""
            print(f"    {v.model or '?':<25}  {(v.group_code or '?'):<8}  "
                  f"{(v.transmission or '?'):<3}  {price_str}{seats_str}")

    if result.error:
        print(f"  error:           {result.error}")


async def main() -> None:
    args = parse_args()
    targets = compute_targets(args)
    provider_key = args.provider_key
    recipe_path = RECIPES_DIR / f"{provider_key}.yaml"

    if not recipe_path.exists():
        print(f"Recipe not found: {recipe_path}")
        print(f"Run run_build_recipe.py --provider-key {provider_key} first.")
        return

    recipe = load_recipe(recipe_path)
    print(f"\n=== recipe: {provider_key} | url={recipe.url} ===")
    print(f"  Discovered: {recipe.discovered_at}")
    print(f"  card_source: {recipe.card_source}  "
          f"cookies_strategy: {recipe.cookies_strategy}")

    # ── Recipe run (no LLM) ───────────────────────────────────────────────────
    log_dir = create_log_dir(provider_key, suffix="_run_recipe")
    print(f"  Logs: {log_dir}")

    recipe_result = await run_recipe(
        recipe, targets, log_dir, headless=not args.visible
    )
    print_recipe_report(recipe_result, label="recipe/no-llm")

    # ── Optional LLM validation ───────────────────────────────────────────────
    if args.validate:
        print("\n  -- LLM validation --")

        # find provider URL from TEST_CASES (by provider_key or first match)
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


if __name__ == "__main__":
    asyncio.run(main())
