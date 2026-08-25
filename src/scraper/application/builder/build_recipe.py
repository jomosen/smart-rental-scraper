"""build_recipe — application-layer orchestration for recipe creation.

Assembles a Recipe from a ScrapeResult (produced by the LLM scraper),
then persists it to the database via ProviderRecipeRepository.

Depends on:
  domain/builder/models  — Recipe, RecipeField, RecipeFieldExtractor (pure)
  infrastructure/repositories/provider_recipe_repository — DB write

The caller (runner script) provides the SQLAlchemy session and the
ProviderRecipeRepository instance (dependency injection) so this module
has no direct DB wiring.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from ...domain.builder.models import Recipe, RecipeField, RecipeFieldExtractor

if TYPE_CHECKING:
    from ...infrastructure.repositories.provider_recipe_repository import (
        ProviderRecipeRepository,
    )

# ── Default keyword lists (re-exported from experiments/ infrastructure) ──────
# These live in experiments/scraper_builder/extraction/results_extractor_dom.py.
# In D2 they will move to infrastructure/builder/; for D1 we define them here
# so build_recipe can emit complete recipes without importing from experiments/.

_DEFAULT_AUTO_KEYWORDS = [
    "automático", "automática", "automatic", "automatique", "automatik",
]
_DEFAULT_MANUAL_KEYWORDS = ["manual", "manuale"]
_DEFAULT_SEAT_KEYWORDS = [
    "plazas", "asientos", "seats", "places", "sitzplätze", "posti", "plaza",
]


# ── Serialization helpers (Recipe ↔ dict) ─────────────────────────────────────

def recipe_to_dict(recipe: Recipe) -> dict:
    """Serialize a Recipe to a plain dict (used for JSONB storage)."""
    return {
        "provider_key": recipe.provider_key,
        "discovered_at": recipe.discovered_at,
        "url": recipe.url,
        "cookies_strategy": recipe.cookies_strategy,
        "form_fields": {
            name: _field_to_dict(rf)
            for name, rf in recipe.form_fields.items()
        },
        "submit_selector": recipe.submit_selector,
        "submit_selector_type": recipe.submit_selector_type,
        "card_source": recipe.card_source,
        "field_extractors": [
            {k: v for k, v in {
                "field": e.field,
                "selector": e.selector,
                "extraction": e.extraction,
                "keywords": e.keywords,
                "auto_keywords": e.auto_keywords,
                "manual_keywords": e.manual_keywords,
            }.items() if v is not None}
            for e in recipe.field_extractors
        ],
        "price_strategy": recipe.price_strategy,
        "refine_url": recipe.refine_url,
        "refine_strategy": recipe.refine_strategy,
        "refine_open_selector": recipe.refine_open_selector,
        "refine_open_selector_type": recipe.refine_open_selector_type,
        "cookie_accept_selector": recipe.cookie_accept_selector,
        "cookie_accept_selector_type": recipe.cookie_accept_selector_type,
    }


def recipe_from_dict(data: dict) -> Recipe:
    """Deserialize a Recipe from a plain dict (used when loading from DB)."""
    form_fields: dict[str, RecipeField] = {}
    for name, fd in data.get("form_fields", {}).items():
        form_fields[name] = RecipeField(
            selector=fd["selector"],
            selector_type=fd.get("selector_type", "css"),
            element_kind=fd.get("element_kind", "input"),
            widget_type=fd.get("widget_type", "unknown"),
            strategy=fd.get("strategy"),
            is_searchable=fd.get("is_searchable"),
            options_container_selector=fd.get("options_container_selector"),
            option_item_selector=fd.get("option_item_selector"),
            match_mode=fd.get("match_mode"),
            accepts_direct_typing=fd.get("accepts_direct_typing"),
            date_format=fd.get("date_format"),
            calendar_container_selector=fd.get("calendar_container_selector"),
            next_month_selector=fd.get("next_month_selector"),
            prev_month_selector=fd.get("prev_month_selector"),
            day_cell_selector=fd.get("day_cell_selector"),
            month_year_label_selector=fd.get("month_year_label_selector"),
            is_range_calendar=fd.get("is_range_calendar"),
        )

    extractors: list[RecipeFieldExtractor] = [
        RecipeFieldExtractor(
            field=e["field"],
            selector=e.get("selector"),
            extraction=e.get("extraction", "text"),
            keywords=e.get("keywords"),
            auto_keywords=e.get("auto_keywords"),
            manual_keywords=e.get("manual_keywords"),
        )
        for e in data.get("field_extractors", [])
    ]

    return Recipe(
        provider_key=data["provider_key"],
        discovered_at=data.get("discovered_at", ""),
        url=data["url"],
        cookies_strategy=data.get("cookies_strategy", "adaptive_poll_heuristic"),
        form_fields=form_fields,
        submit_selector=data["submit_selector"],
        submit_selector_type=data.get("submit_selector_type", "css"),
        card_source=data.get("card_source", "mark_valid_cards"),
        field_extractors=extractors,
        price_strategy=data.get("price_strategy", ""),
        refine_url=data.get("refine_url"),
        refine_strategy=data.get("refine_strategy", "none"),
        refine_open_selector=data.get("refine_open_selector"),
        refine_open_selector_type=data.get("refine_open_selector_type", "css"),
        cookie_accept_selector=data.get("cookie_accept_selector"),
        cookie_accept_selector_type=data.get("cookie_accept_selector_type", "css"),
    )


def _field_to_dict(rf: RecipeField) -> dict:
    return {
        "selector": rf.selector,
        "selector_type": rf.selector_type,
        "element_kind": rf.element_kind,
        "widget_type": rf.widget_type,
        "strategy": rf.strategy,
        "is_searchable": rf.is_searchable,
        "options_container_selector": rf.options_container_selector,
        "option_item_selector": rf.option_item_selector,
        "match_mode": rf.match_mode,
        "accepts_direct_typing": rf.accepts_direct_typing,
        "date_format": rf.date_format,
        "calendar_container_selector": rf.calendar_container_selector,
        "next_month_selector": rf.next_month_selector,
        "prev_month_selector": rf.prev_month_selector,
        "day_cell_selector": rf.day_cell_selector,
        "month_year_label_selector": rf.month_year_label_selector,
        "is_range_calendar": rf.is_range_calendar,
    }


# ── Application function ──────────────────────────────────────────────────────

def build_recipe(
    provider_key: str,
    scrape_result,            # duck-typed ScrapeResult from experiments/scraper_engine
    log_dir: Path,
    repo: "ProviderRecipeRepository",
    provider_id: int,
    refine_url: str | None = None,
    refine_strategy: str = "none",
) -> Recipe:
    """Build a Recipe from a ScrapeResult and persist it to the database.

    Widget info (widget_type, calendar selectors, match_mode, etc.) is read
    from log_dir/form_widget_infos.json, written there by form_fill_orchestrator.

    Parameters
    ----------
    provider_key:    Logical key (e.g. 'centauro') matching providers.code.
    scrape_result:   Output of scrape() from scraper_engine (duck-typed).
    log_dir:         Directory where form_widget_infos.json was written.
    repo:            ProviderRecipeRepository bound to an open Session.
    provider_id:     DB id of the providers row for this provider_key.
    refine_url:      Deep-link to the provider's "edit search" page. When set
                     with refine_strategy='navigate_and_change_dates', searches
                     after the first change only the dates on that page instead
                     of re-filling the whole form on the homepage. See
                     RecipeScraper._refine_form and recipe_executor.refine_dates.
    refine_strategy: 'navigate_and_change_dates' | 'in_place' | 'none'.

    Returns the Recipe that was saved.
    """
    if scrape_result.form_fields is None:
        raise ValueError("ScrapeResult.form_fields is None — was the form filled?")

    # ── Widget info written by form_fill_orchestrator ─────────────────────────
    widget_data: dict = {}
    winfo_path = log_dir / "form_widget_infos.json"
    if winfo_path.exists():
        widget_data = json.loads(winfo_path.read_text(encoding="utf-8"))

    # ── Form fields ───────────────────────────────────────────────────────────
    recipe_fields: dict[str, RecipeField] = {}
    ff = scrape_result.form_fields

    for name, ifield in [
        ("pickup_location", ff.pickup_location),
        ("pickup_date",     ff.pickup_date),
        ("return_date",     ff.return_date),
        ("pickup_time",     ff.pickup_time),
        ("return_time",     ff.return_time),
    ]:
        if ifield is None:
            continue
        wd = widget_data.get(name, {})
        recipe_fields[name] = RecipeField(
            selector=ifield.selector,
            selector_type=ifield.selector_type,
            element_kind=ifield.element_kind,
            widget_type=wd.get("widget_type", "unknown"),
            strategy=wd.get("strategy"),
            is_searchable=wd.get("is_searchable"),
            options_container_selector=wd.get("options_container_selector"),
            option_item_selector=wd.get("option_item_selector"),
            match_mode=wd.get("match_mode"),
            accepts_direct_typing=wd.get("accepts_direct_typing"),
            date_format=wd.get("date_format"),
            calendar_container_selector=wd.get("calendar_container_selector"),
            next_month_selector=wd.get("next_month_selector"),
            prev_month_selector=wd.get("prev_month_selector"),
            day_cell_selector=wd.get("day_cell_selector"),
            month_year_label_selector=wd.get("month_year_label_selector"),
            is_range_calendar=wd.get("is_range_calendar"),
        )

    # ── Submit selector ───────────────────────────────────────────────────────
    sb = ff.submit_button
    submit_sel = sb.selector if sb else ""
    submit_sel_type = sb.selector_type if sb else "css"

    # ── Field extractors ──────────────────────────────────────────────────────
    # Selector-based fields (model, group_code, …) from LLM discovery.
    # Semantic fields always appended with fixed strategies → recipe is self-contained.
    # seats/transmission CSS selectors from discovery are KEPT as fallbacks:
    # the aria semantics fire first, the selector covers sites that expose the
    # value in plain markup (spec cells with data-title, icon classes) — the
    # gap that left recordgo with 0% transmission until recipe v9.
    _SELECTOR_SKIP = frozenset({"price_final", "currency"})
    _FALLBACK_FIELDS = frozenset({"seats", "transmission"})
    extractors: list[RecipeFieldExtractor] = []
    if scrape_result.results_structure:
        for fs in scrape_result.results_structure.field_selectors:
            if fs.field in _SELECTOR_SKIP:
                continue
            if fs.field in _FALLBACK_FIELDS and not fs.selector:
                continue  # semantic-only proposal; the fixed strategies below cover it
            extractors.append(RecipeFieldExtractor(
                field=fs.field,
                selector=fs.selector if fs.selector else None,
                extraction=fs.extraction,
            ))

    extractors.append(RecipeFieldExtractor(
        field="transmission",
        selector=None,
        extraction="aria_keyword_transmission",
        auto_keywords=_DEFAULT_AUTO_KEYWORDS,
        manual_keywords=_DEFAULT_MANUAL_KEYWORDS,
    ))
    extractors.append(RecipeFieldExtractor(
        field="seats",
        selector=None,
        extraction="aria_keyword",
        keywords=_DEFAULT_SEAT_KEYWORDS,
    ))
    extractors.append(RecipeFieldExtractor(
        field="price_final",
        selector=None,
        extraction="price_cascade",
    ))

    price_strategy = (
        scrape_result.results_structure.price_strategy
        if scrape_result.results_structure else
        "three_level_cascade: field_selector → aria-label → text-node struck/non-struck"
    )

    recipe = Recipe(
        provider_key=provider_key,
        discovered_at=datetime.now(timezone.utc).isoformat(),
        url=scrape_result.url,
        cookies_strategy="adaptive_poll_heuristic",
        form_fields=recipe_fields,
        submit_selector=submit_sel,
        submit_selector_type=submit_sel_type,
        card_source="mark_valid_cards",
        field_extractors=extractors,
        price_strategy=price_strategy,
        refine_url=refine_url,
        refine_strategy=refine_strategy,
    )

    repo.save_recipe(provider_id=provider_id, recipe=recipe)
    return recipe
