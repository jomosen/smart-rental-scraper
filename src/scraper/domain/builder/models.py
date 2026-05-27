"""Recipe domain models — pure dataclasses, no I/O, no external dependencies."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RecipeField:
    """Everything needed to fill one form field without LLM classification."""
    selector: str
    selector_type: str              # "css"
    element_kind: str               # "input" | "div" | "select" | "other"
    widget_type: str                # "autocomplete" | "custom_dropdown" |
                                    # "custom_calendar" | "text_input" | "native_date"
    strategy: str | None = None     # "range_calendar_autofill" → skip explicit fill
    is_searchable: bool | None = None
    options_container_selector: str | None = None
    option_item_selector: str | None = None
    match_mode: str | None = None   # "fuzzy" | "exact"
    accepts_direct_typing: bool | None = None
    date_format: str | None = None
    calendar_container_selector: str | None = None
    next_month_selector: str | None = None
    prev_month_selector: str | None = None
    day_cell_selector: str | None = None
    month_year_label_selector: str | None = None
    is_range_calendar: bool | None = None


@dataclass
class RecipeFieldExtractor:
    """Selector + extraction strategy for one vehicle-card field."""
    field: str                      # "model" | "group_code" | "seats" | "transmission" | "price_final"
    selector: str | None            # CSS selector relative to card; None for semantic strategies
    extraction: str                 # "text" | "attribute:X" | "regex:Y"
                                    # | "aria_keyword"              → seats
                                    # | "aria_keyword_transmission" → transmission
                                    # | "price_cascade"             → price_final
    keywords: list[str] | None = None          # aria_keyword: seat keyword list
    auto_keywords: list[str] | None = None     # aria_keyword_transmission: auto keywords
    manual_keywords: list[str] | None = None   # aria_keyword_transmission: manual keywords


@dataclass
class Recipe:
    """Complete recipe for one provider — config, not code."""
    provider_key: str
    discovered_at: str              # ISO-8601 timestamp
    url: str
    cookies_strategy: str           # "adaptive_poll_heuristic"
    form_fields: dict[str, RecipeField]
    submit_selector: str
    submit_selector_type: str       # "css"
    card_source: str                # "mark_valid_cards"
    field_extractors: list[RecipeFieldExtractor]
    price_strategy: str
