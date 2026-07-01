"""Set refine_strategy / refine_url on a provider's active recipe.

Manual post-rebuild tweak. `run_build_recipe.py` discovers the search form but
sets `refine_strategy='none'`, so every probe/extraction search re-fills the
WHOLE form on the homepage — which, on a reused browser session, can fail when
the provider's SPA doesn't remount the location widget (observed on centauro
after its 2026-07 site change), forcing a slow browser relaunch per search.

For providers that expose a dedicated "edit search" deep-link (centauro:
https://www.centauro.net/reserva/lugar-y-fechas/, reached by clicking the dates
on the results page), `navigate_and_change_dates` makes searches 2+ navigate to
that page and change ONLY the dates — reusing the session efficiently and never
touching the fragile homepage location widget. If the refine page lacks the
recipe's date selectors, refine fails and the caller falls back to a full
submit, so setting this is safe (worst case = current behaviour).

Writes a new active recipe version (append-versioned) to the DB that
APP_DATABASE_URL points at — run it against local first, then production.

Usage:
    python set_recipe_refine.py --provider centauro \
        --url https://www.centauro.net/reserva/lugar-y-fechas/ \
        --strategy navigate_and_change_dates
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

# ── Make src/ importable from experiments/ context ───────────────────────────
_PROJECT_ROOT = Path(__file__).parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select

from src.saas.infrastructure.persistence.engine import app_engine
from src.saas.infrastructure.persistence.session import make_session_factory
from src.saas.infrastructure.persistence.models.catalog import Provider
from src.scraper.infrastructure.repositories.provider_recipe_repository import (
    ProviderRecipeRepository,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Set refine_strategy/refine_url on a provider's active recipe."
    )
    p.add_argument("--provider", required=True, help="providers.code (e.g. centauro)")
    p.add_argument("--url", default=None,
                   help="refine_url deep-link (required for navigate_and_change_dates)")
    p.add_argument("--strategy", default="navigate_and_change_dates",
                   choices=["navigate_and_change_dates", "in_place", "none"])
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if args.strategy == "navigate_and_change_dates" and not args.url:
        raise SystemExit("--url is required for strategy=navigate_and_change_dates")

    factory = make_session_factory(app_engine())
    session = factory()
    try:
        provider = session.scalar(select(Provider).where(Provider.code == args.provider))
        if provider is None:
            raise SystemExit(f"No provider with code={args.provider!r}")

        repo = ProviderRecipeRepository(session)
        recipe = repo.get_active_recipe(provider.id)
        if recipe is None:
            raise SystemExit(f"No active recipe for provider_id={provider.id}")

        updated = replace(recipe, refine_strategy=args.strategy, refine_url=args.url)
        row = repo.save_recipe(provider.id, updated)
        session.commit()
        print(
            f"[OK] provider={args.provider} (id={provider.id}) -> recipe v{row.version}\n"
            f"     refine_strategy={updated.refine_strategy!r}\n"
            f"     refine_url={updated.refine_url!r}"
        )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
