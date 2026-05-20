"""Dispatch from DateWidgetInfo to the correct DateFiller implementation."""
from __future__ import annotations

from date_analysis.date_widget_classifier import DateWidgetInfo
from filling.base_filler import DateFiller
from filling.fillers.date_calendar_filler import DateCalendarFiller
from filling.fillers.date_native_filler import DateNativeFiller
from filling.fillers.date_text_filler import DateTextFiller


class UnsupportedDateWidgetError(Exception):
    pass


def get_date_filler(date_widget: DateWidgetInfo) -> DateFiller:
    """
    Return the appropriate DateFiller for *date_widget*.

    Priority order:
    1. native_date  → DateNativeFiller (most reliable)
    2. accepts_direct_typing  → DateTextFiller (robust; covers text_input and
       hybrid custom_calendar widgets that also accept typing)
    3. custom_calendar  → DateCalendarFiller
    """
    if date_widget.widget_type == "native_date":
        return DateNativeFiller()

    if date_widget.widget_type == "text_input" or date_widget.accepts_direct_typing:
        return DateTextFiller()

    if date_widget.widget_type == "custom_calendar":
        return DateCalendarFiller()

    raise UnsupportedDateWidgetError(
        f"No date filler for widget_type={date_widget.widget_type!r} "
        f"(accepts_direct_typing={date_widget.accepts_direct_typing}). "
        f"Implement a new DateFiller and register it here."
    )
