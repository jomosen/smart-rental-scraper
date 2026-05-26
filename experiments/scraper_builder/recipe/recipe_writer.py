"""Build a Recipe from a ScrapeResult and write it to YAML."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from scraper_engine import ScrapeResult
from .models import Recipe, RecipeField, RecipeFieldExtractor


def build_recipe(
    provider_key: str,
    result: ScrapeResult,
    log_dir: Path,
) -> Recipe:
    """
    Construct a Recipe from a successful ScrapeResult.

    Widget info (widget_type, calendar selectors, match_mode, etc.) is read
    from log_dir/form_widget_infos.json, which form_fill_orchestrator writes
    after each widget classification.  form_fields supplies the stable field
    selectors (id/name/role-based).

    IMPORTANT: card_source is always "mark_valid_cards" — the structural
    card_selector from the LLM (fragile, breaks on re-renders) is never stored.
    Seats and transmission are omitted from field_extractors because the DOM
    extractor handles them via semantic aria-label matching without needing a
    selector.
    """
    if result.form_fields is None:
        raise ValueError("ScrapeResult.form_fields is None — was the form filled?")

    # ── Widget info written by form_fill_orchestrator ─────────────────────────
    widget_data: dict = {}
    winfo_path = log_dir / "form_widget_infos.json"
    if winfo_path.exists():
        widget_data = json.loads(winfo_path.read_text(encoding="utf-8"))

    # ── Form fields ───────────────────────────────────────────────────────────
    recipe_fields: dict[str, RecipeField] = {}
    ff = result.form_fields

    _FIELD_NAMES = [
        ("pickup_location", ff.pickup_location),
        ("pickup_date",     ff.pickup_date),
        ("return_date",     ff.return_date),
        ("pickup_time",     ff.pickup_time),
        ("return_time",     ff.return_time),
    ]
    for name, ifield in _FIELD_NAMES:
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

    # ── Field extractors from results_structure ───────────────────────────────
    # Only include fields that need an explicit selector.
    # seats, transmission, price_final, currency → handled by the DOM extractor's
    # built-in semantic (aria-label) and three-level price cascade — no selector needed.
    _SEMANTIC_FIELDS = frozenset({"seats", "transmission", "price_final", "currency"})
    extractors: list[RecipeFieldExtractor] = []
    if result.results_structure:
        for fs in result.results_structure.field_selectors:
            if fs.field in _SEMANTIC_FIELDS:
                continue
            extractors.append(RecipeFieldExtractor(
                field=fs.field,
                selector=fs.selector if fs.selector else None,
                extraction=fs.extraction,
            ))

    price_strategy = (
        result.results_structure.price_strategy
        if result.results_structure else
        "three_level_cascade: field_selector → aria-label → text-node struck/non-struck"
    )

    return Recipe(
        provider_key=provider_key,
        discovered_at=datetime.now(timezone.utc).isoformat(),
        url=result.url,
        cookies_strategy="adaptive_poll_heuristic",
        form_fields=recipe_fields,
        submit_selector=submit_sel,
        submit_selector_type=submit_sel_type,
        card_source="mark_valid_cards",
        field_extractors=extractors,
        price_strategy=price_strategy,
    )


def write_recipe(recipe: Recipe, path: Path) -> None:
    """Serialize Recipe to human-readable YAML."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _to_dict(recipe)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False,
                  default_flow_style=False)


# ── Serialization helpers ─────────────────────────────────────────────────────

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


def _to_dict(recipe: Recipe) -> dict:
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
            {"field": e.field, "selector": e.selector, "extraction": e.extraction}
            for e in recipe.field_extractors
        ],
        "price_strategy": recipe.price_strategy,
    }
