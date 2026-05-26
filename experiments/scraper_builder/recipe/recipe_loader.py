"""Load a Recipe from YAML."""
from __future__ import annotations

from pathlib import Path

import yaml

from .models import Recipe, RecipeField, RecipeFieldExtractor


def load_recipe(path: Path) -> Recipe:
    """Deserialize a Recipe from a YAML file written by write_recipe()."""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

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
    )
