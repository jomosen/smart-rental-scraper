"""Dispatch from WidgetInfo to the correct LocationFiller implementation."""
from __future__ import annotations

from ..field_analysis.widget_classifier import WidgetInfo
from .base_filler import LocationFiller
from .fillers.autocomplete_filler import AutocompleteFiller
from .fillers.custom_dropdown_filler import CustomDropdownFiller


class UnsupportedWidgetError(Exception):
    pass


def get_filler_for_widget(
    widget: WidgetInfo,
    match_mode: str = "fuzzy",
) -> LocationFiller:
    """
    Return the appropriate filler for *widget*.

    *match_mode* is forwarded to AutocompleteFiller:
      "fuzzy" (default) — rapidfuzz, for locations.
      "exact"           — normalized exact match, for times and discrete values.

    Raises UnsupportedWidgetError when no implementation exists yet.
    """
    # Any searchable widget uses the autocomplete strategy regardless of whether
    # the LLM classified it as "autocomplete" or "custom_dropdown" — the behavioral
    # contract is the same: click to focus, type to filter, click option.
    if widget.is_searchable:
        return AutocompleteFiller(match_mode=match_mode)

    # Non-searchable widgets: open → read full list → match → scroll if virtualized
    if widget.widget_type in ("custom_dropdown", "autocomplete", "unknown"):
        return CustomDropdownFiller(match_mode=match_mode)

    # TODO: add more fillers as needed
    # "native_select" → NativeSelectFiller
    # "custom_modal"  → CustomModalFiller
    # "datalist"      → DatalistFiller

    raise UnsupportedWidgetError(
        f"No filler implemented for widget_type={widget.widget_type!r} "
        f"with is_searchable=False. "
        f"options_container={widget.options_container_selector!r}"
    )
