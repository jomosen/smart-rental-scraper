"""Set cookie_accept_selector on a provider's active recipe.

Manual post-rebuild tweak. At runtime the recipe closes the cookie banner with
a multilingual text heuristic. On CMPs that inject the banner asynchronously
(e.g. centauro / CookieFirst) the real "Aceptar todo" button can render a beat
late; the heuristic then fell through to a generic token and mis-clicked a
"cookie policy" link, navigating off the search page so the location dropdown
was gone → "Widget did not open" on the first search of a session.

Setting cookie_accept_selector makes the runtime click that exact control
directly (waiting for it to appear) before ever falling back to the heuristic.
The build already identifies this selector via the LLM cookie closer; this
script backfills it onto an existing recipe without a full rebuild. If the
selector never shows or fails, the runtime still falls back to the (now
whole-word-safe) heuristic, so setting this is safe (worst case = fallback).

Writes a new active recipe version (append-versioned) to the DB that
APP_DATABASE_URL points at — run it against local first, then production.

Usage:
    python set_recipe_cookie.py --provider centauro \
        --selector "[data-testid='actionButton-accept']"
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
        description="Set cookie_accept_selector on a provider's active recipe."
    )
    p.add_argument("--provider", required=True, help="providers.code (e.g. centauro)")
    p.add_argument("--selector", required=True,
                   help="CSS selector for the cookie accept button")
    p.add_argument("--selector-type", default="css", choices=["css", "xpath"])
    return p.parse_args()


def main() -> None:
    args = parse_args()

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

        updated = replace(
            recipe,
            cookie_accept_selector=args.selector,
            cookie_accept_selector_type=args.selector_type,
        )
        row = repo.save_recipe(provider.id, updated)
        session.commit()
        print(
            f"[OK] provider={args.provider} (id={provider.id}) -> recipe v{row.version}\n"
            f"     cookie_accept_selector={updated.cookie_accept_selector!r}\n"
            f"     cookie_accept_selector_type={updated.cookie_accept_selector_type!r}"
        )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
