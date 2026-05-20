"""Dispatch from WidgetInfo to the correct LocationFiller implementation."""
from __future__ import annotations

from field_analysis.widget_classifier import WidgetInfo
from filling.base_filler import LocationFiller
from filling.fillers.autocomplete_filler import AutocompleteFiller


class UnsupportedWidgetError(Exception):
    pass


def get_filler_for_widget(widget: WidgetInfo) -> LocationFiller:
    """
    Return the appropriate filler for *widget*.
    Raises UnsupportedWidgetError when no implementation exists yet.
    """
    # Any searchable widget uses the autocomplete strategy regardless of whether
    # the LLM classified it as "autocomplete" or "custom_dropdown" — the behavioral
    # contract is the same: click to focus, type to filter, click option.
    if widget.is_searchable:
        return AutocompleteFiller()

    # TODO: add more fillers as needed
    # non-searchable "custom_dropdown" → CustomDropdownFiller (click-only)
    # "native_select"                  → NativeSelectFiller
    # "custom_modal"                   → CustomModalFiller
    # "datalist"                       → DatalistFiller

    raise UnsupportedWidgetError(
        f"No filler implemented for widget_type={widget.widget_type!r} "
        f"with is_searchable=False. "
        f"options_container={widget.options_container_selector!r}"
    )
