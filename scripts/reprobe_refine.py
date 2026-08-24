"""Re-run refine discovery for an existing recipe provider WITHOUT rebuilding.

Loads the active recipe from the DB, reaches the results page with it, runs
the builder's discover_refine_link (tries in_place first, then the LLM
edit-control path — at most one LLM call), VERIFIES the candidate with a real
date change, and saves a new active recipe version only on confirmation.

Usage:
    python scripts/reprobe_refine.py <provider_code> [--location NAME]

Prerequisites: DB up with the provider's recipe active; ANTHROPIC_API_KEY in
.env for the LLM fallback path.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text

from src.scraper.infrastructure.builder.location_explorer import create_log_dir
from src.scraper.infrastructure.builder.browser_session import BrowserSession
from src.scraper.infrastructure.builder.recipe_executor import run_recipe
from src.scraper.infrastructure.builder.refine_discovery import discover_refine_link
from src.scraper.infrastructure.repositories.provider_recipe_repository import (
    ProviderRecipeRepository,
)
from src.saas.infrastructure.persistence.engine import app_engine
from src.saas.infrastructure.persistence.session import make_session_factory

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("provider_code")
    ap.add_argument("--location", default="Alicante")
    ap.add_argument("--visible", action="store_true", help="show the browser window")
    args = ap.parse_args()

    factory = make_session_factory(app_engine())

    session = factory()
    try:
        row = session.execute(
            text("select id from providers where code = :c"), {"c": args.provider_code}
        ).fetchone()
        if not row:
            sys.exit(f"provider '{args.provider_code}' not found")
        pid = row[0]
        recipe = ProviderRecipeRepository(session).get_active_recipe(pid)
    finally:
        session.close()
    print(f"provider_id={pid}  current refine: strategy={recipe.refine_strategy!r} "
          f"url={recipe.refine_url!r}")

    pickup = date.today() + timedelta(days=48)
    targets = {
        "location": args.location,
        "pickup_date": pickup,
        "return_date": pickup + timedelta(days=7),
        "pickup_time": "10:00",
        "return_time": "10:00",
    }
    log_dir = create_log_dir(args.provider_code, suffix="_refine_reprobe")
    print(f"logs: {log_dir}")

    bs = BrowserSession(headless=not args.visible)
    await bs.__aenter__()
    try:
        res = await run_recipe(recipe, targets, log_dir, session=bs)
        if not res.success:
            print(f"FAIL: could not reach results (phase={res.failed_phase!r}) — no changes")
            return
        url, strategy, open_sel = await discover_refine_link(bs, recipe, targets, log_dir)
    finally:
        await bs.__aexit__(None, None, None)

    print(f"discovered: strategy={strategy!r} url={url!r} open_selector={open_sel!r}")
    if strategy == "none":
        print("nothing confirmed — recipe left unchanged")
        return

    session = factory()
    try:
        repo = ProviderRecipeRepository(session)
        active = repo.get_active_recipe(pid)
        row = repo.save_recipe(pid, replace(
            active,
            refine_url=url,
            refine_strategy=strategy,
            refine_open_selector=open_sel,
        ))
        session.commit()
        print(f"saved recipe v{row.version} (active) with refine strategy={strategy!r} "
              f"open_selector={open_sel!r}")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    asyncio.run(main())
