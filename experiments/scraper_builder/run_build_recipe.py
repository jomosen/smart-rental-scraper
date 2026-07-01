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

from dataclasses import replace

from src.scraper.infrastructure.builder.location_explorer import create_log_dir
from src.scraper.infrastructure.builder.scraper_engine import scrape
from src.scraper.infrastructure.builder.browser_session import BrowserSession
from src.scraper.infrastructure.builder.recipe_executor import run_recipe
from src.scraper.infrastructure.builder.refine_discovery import discover_refine_link

from src.scraper.application.builder.build_recipe import build_recipe
from src.scraper.application.builder.provision import ProviderProvisioningService
from src.scraper.infrastructure.repositories.provider_recipe_repository import (
    ProviderRecipeRepository,
)
from src.saas.infrastructure.persistence.engine import app_engine
from src.saas.infrastructure.persistence.session import make_session_factory
from src.saas.infrastructure.persistence.repositories import (
    ProviderRepository,
    ProviderLocationRepository,
    ProviderRateRepository,
)

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
                   help="providers.code for this site (default: site name)")
    p.add_argument("--url", default=None,
                   help="Build an ad-hoc provider at this URL (requires --provider-key). "
                        "Default: the built-in TEST_CASES.")
    # ── Refine (searches after the first change only the dates) ──────────────
    p.add_argument("--refine-url", default=None,
                   help="Explicit 'edit search' deep-link. Skips auto-discovery.")
    p.add_argument("--refine-strategy", default="navigate_and_change_dates",
                   choices=["navigate_and_change_dates", "in_place", "none"],
                   help="Strategy to pair with --refine-url (default: navigate_and_change_dates)")
    p.add_argument("--no-auto-refine", dest="auto_refine", action="store_false",
                   help="Disable LLM auto-discovery of the edit-search deep-link")
    p.set_defaults(auto_refine=True)
    return p.parse_args()


async def _auto_discover_refine(
    recipe, targets: dict, headless: bool, provider_name: str
) -> tuple[str | None, str]:
    """Reach the results page with *recipe*, then probe for the refine strategy
    (navigate_and_change_dates | in_place). Best-effort — never raises.
    Returns (refine_url, strategy)."""
    log_dir = create_log_dir(provider_name, suffix="_refine_probe")
    bs = BrowserSession(headless=headless)
    await bs.__aenter__()
    try:
        res = await run_recipe(recipe, targets, log_dir, session=bs)
        if not res.success:
            print(f"  [auto-refine] could not reach results "
                  f"(phase={res.failed_phase!r}) — skipping")
            return None, "none"
        return await discover_refine_link(bs, recipe, targets, log_dir)
    except Exception as exc:  # noqa: BLE001 — probe must never break the build
        print(f"  [auto-refine] probe error: {exc}")
        return None, "none"
    finally:
        await bs.__aexit__(None, None, None)


async def main() -> None:
    args = parse_args()
    targets = compute_targets(args)

    engine = app_engine()
    factory = make_session_factory(engine)

    if args.url and not args.provider_key:
        raise SystemExit("--url requires --provider-key (used as providers.code)")
    cases = [(args.provider_key, args.url)] if args.url else TEST_CASES

    for name, url in cases:
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

        # Explicit --refine-url wins; otherwise start at 'none' and let the
        # Level-2 auto-discovery below fill it in.
        refine_url = args.refine_url
        refine_strategy = args.refine_strategy if args.refine_url else "none"

        session = factory()
        try:
            # 1. Ensure provider + location + rate rows exist (idempotent)
            provisioning_svc = ProviderProvisioningService(
                provider_repo=ProviderRepository(session),
                location_repo=ProviderLocationRepository(session),
                rate_repo=ProviderRateRepository(session),
            )
            prov = provisioning_svc.ensure(provider_key, targets, base_url=url)

            # 2. Build recipe from discovery result and save to DB
            repo = ProviderRecipeRepository(session)
            recipe = build_recipe(
                provider_key, result, log_dir, repo, prov.provider_id,
                refine_url=refine_url, refine_strategy=refine_strategy,
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        print(f"  Recipe saved to DB (provider_id={prov.provider_id})")
        print(f"  form_fields:      {list(recipe.form_fields)}")
        print(f"  field_extractors: {[e.field for e in recipe.field_extractors]}")
        print(f"  card_source:      {recipe.card_source}")
        print(f"  refine:           strategy={recipe.refine_strategy!r} url={recipe.refine_url!r}")

        # 3. Level-2: auto-discover the edit-search deep-link when not supplied.
        #    Runs one extra search + one LLM call. Best-effort: on any failure
        #    the recipe simply keeps its full-resubmit behaviour.
        if not args.refine_url and args.auto_refine:
            print("  [auto-refine] probing results page for an edit-search deep-link…")
            disc_url, disc_strategy = await _auto_discover_refine(
                recipe, targets, not args.visible, name
            )
            if disc_url:
                session = factory()
                try:
                    repo = ProviderRecipeRepository(session)
                    active = repo.get_active_recipe(prov.provider_id)
                    updated = replace(
                        active, refine_url=disc_url, refine_strategy=disc_strategy
                    )
                    row = repo.save_recipe(prov.provider_id, updated)
                    session.commit()
                    print(f"  [auto-refine] OK -> recipe v{row.version}  "
                          f"strategy={disc_strategy!r}  url={disc_url}")
                except Exception:
                    session.rollback()
                    raise
                finally:
                    session.close()
            else:
                print("  [auto-refine] no edit-search deep-link found — "
                      "recipe uses full re-submit")


if __name__ == "__main__":
    asyncio.run(main())
