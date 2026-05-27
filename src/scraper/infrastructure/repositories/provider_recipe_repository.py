"""ProviderRecipeRepository — DB-backed storage for versioned scraping recipes.

Append-versioning semantics:
  save_recipe   → insert new row (version N+1, active=true),
                  flip previous active row to active=false — one transaction.
  get_active_recipe → return the single active recipe for a provider, or None.

The table is catalog-scoped (no tenant_id, no RLS).  The session is provided
by the caller; this class contains no engine wiring.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ....saas.infrastructure.persistence.models.catalog import ProviderRecipe
from ...domain.builder.models import Recipe
from ...application.builder.build_recipe import recipe_from_dict, recipe_to_dict


class ProviderRecipeRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    # ── Public API ────────────────────────────────────────────────────────────

    def save_recipe(self, provider_id: int, recipe: Recipe) -> ProviderRecipe:
        """Append a new recipe version and deactivate the previous active one.

        Steps (all in the caller's transaction):
          1. Compute next version number.
          2. Flip any existing active row to active=false.
          3. Insert new row with active=true.

        Returns the newly inserted ProviderRecipe ORM row.
        """
        next_version = self._next_version(provider_id)

        # Deactivate existing active recipe (if any)
        self._s.execute(
            update(ProviderRecipe)
            .where(
                ProviderRecipe.provider_id == provider_id,
                ProviderRecipe.active.is_(True),
            )
            .values(active=False)
        )

        row = ProviderRecipe(
            provider_id=provider_id,
            version=next_version,
            recipe_jsonb=recipe_to_dict(recipe),
            discovered_at=_parse_discovered_at(recipe.discovered_at),
            active=True,
        )
        self._s.add(row)
        self._s.flush()  # populate row.id without committing
        return row

    def get_active_recipe(self, provider_id: int) -> Optional[Recipe]:
        """Return the active Recipe for provider_id, or None if none exists."""
        row = self._s.scalar(
            select(ProviderRecipe).where(
                ProviderRecipe.provider_id == provider_id,
                ProviderRecipe.active.is_(True),
            )
        )
        if row is None:
            return None
        return recipe_from_dict(row.recipe_jsonb)

    def list_versions(self, provider_id: int) -> list[ProviderRecipe]:
        """Return all recipe rows for provider_id ordered by version desc."""
        return list(
            self._s.scalars(
                select(ProviderRecipe)
                .where(ProviderRecipe.provider_id == provider_id)
                .order_by(ProviderRecipe.version.desc())
            )
        )

    # ── Internals ─────────────────────────────────────────────────────────────

    def _next_version(self, provider_id: int) -> int:
        """Return max(version) + 1 for provider_id, or 1 if no rows exist."""
        from sqlalchemy import func
        result = self._s.scalar(
            select(func.max(ProviderRecipe.version)).where(
                ProviderRecipe.provider_id == provider_id
            )
        )
        return (result or 0) + 1


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_discovered_at(iso_str: str):
    """Parse an ISO-8601 string to a timezone-aware datetime, or return None."""
    if not iso_str:
        return None
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None
