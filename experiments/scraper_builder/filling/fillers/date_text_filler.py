"""Date filler for text inputs: types the date in the detected format."""
from __future__ import annotations

import time
from datetime import date

from browser_session import BrowserSession
from date_analysis.date_widget_classifier import DateWidgetInfo
from field_analysis.field_identifier import IdentifiedField
from filling.base_filler import DateFiller, FillResult
from filling.date_utils import format_date
from session_logger import SessionLogger


class DateTextFiller(DateFiller):
    """
    Types the date directly into the input field.
    Works for any text input that accepts typed dates.
    The target format comes from DateWidgetInfo.date_format.
    """

    strategy_name = "date_text"

    async def fill(
        self,
        session: BrowserSession,
        field: IdentifiedField,
        date_widget: DateWidgetInfo,
        target_date: date,
        logger: SessionLogger,
    ) -> FillResult:
        start = time.monotonic()

        if date_widget.date_format is None:
            logger.log("date_text_format_assumed", assumed="DD/MM/YYYY")

        formatted = format_date(target_date, date_widget.date_format)
        logger.log("filler_started", strategy=self.strategy_name,
                   formatted=formatted, target=target_date.isoformat())

        await session.type_text(field.selector, formatted, field.selector_type)

        # Trigger blur/change so the page registers the value
        try:
            await session.page.keyboard.press("Tab")
        except Exception:
            pass

        await session.wait_ms(800)
        logger.log("date_text_filled", formatted=formatted)

        return FillResult(
            success=True,
            strategy_used=self.strategy_name,
            target_value=formatted,
            matched_option=formatted,
            state_changes=None,
            duration_seconds=time.monotonic() - start,
            error=None,
        )
