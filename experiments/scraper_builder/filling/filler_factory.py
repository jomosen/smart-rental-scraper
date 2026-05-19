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
    if widget.widget_type == "autocomplete":
        return AutocompleteFiller()

    # TODO: add more fillers as needed
    # "custom_dropdown" → CustomDropdownFiller (with/without searchable)
    # "native_select"   → NativeSelectFiller
    # "custom_modal"    → CustomModalFiller
    # "datalist"        → DatalistFiller

    raise UnsupportedWidgetError(
        f"No filler implemented for widget_type={widget.widget_type!r}. "
        f"Widget details: is_searchable={widget.is_searchable}, "
        f"options_container={widget.options_container_selector!r}"
    )
