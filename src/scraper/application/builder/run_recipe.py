"""run_recipe_from_db — application-layer orchestration for recipe execution.

Reads the active recipe for a provider from the database, then delegates
to the caller-supplied executor (the actual browser-based runner).
This keeps the application layer free of Playwright / infrastructure imports.

In D1, the executor is experiments/scraper_builder/recipe/recipe_scraper.run_recipe.
In D2, it will be src/scraper/infrastructure/builder/recipe_scraper.run_recipe.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from ...infrastructure.repositories.provider_recipe_repository import (
        ProviderRecipeRepository,
    )


async def run_recipe_from_db(
    provider_id: int,
    targets: dict,
    log_dir: Path,
    headless: bool,
    repo: "ProviderRecipeRepository",
    executor: Callable,
):
    """Execute the active recipe for provider_id using the supplied executor.

    Parameters
    ----------
    provider_id:  DB id of the providers row.
    targets:      Scrape targets dict (location, dates, times).
    log_dir:      Directory for session logs.
    headless:     Whether to hide the browser window.
    repo:         ProviderRecipeRepository bound to an open Session.
    executor:     Async callable (recipe, targets, log_dir, headless) → ScrapeResult.
                  In D1: experiments/recipe/recipe_scraper.run_recipe.
                  In D2: infrastructure/builder/recipe_scraper.run_recipe.

    Returns the ScrapeResult produced by the executor.
    Raises ValueError if no active recipe exists for provider_id.
    """
    recipe = repo.get_active_recipe(provider_id)
    if recipe is None:
        raise ValueError(
            f"No active recipe found for provider_id={provider_id}. "
            "Run run_build_recipe.py first."
        )

    return await executor(recipe, targets, log_dir, headless)
