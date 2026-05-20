"""Date filler for <input type="date"> native browser inputs."""
from __future__ import annotations

import time
from datetime import date

from browser_session import BrowserSession
from date_analysis.date_widget_classifier import DateWidgetInfo
from field_analysis.field_identifier import IdentifiedField
from filling.base_filler import DateFiller, FillResult
from session_logger import SessionLogger


class DateNativeFiller(DateFiller):
    """
    Fills a native <input type="date"> using Playwright's page.fill(),
    which accepts ISO format (YYYY-MM-DD) regardless of browser locale.
    """

    strategy_name = "date_native"

    async def fill(
        self,
        session: BrowserSession,
        field: IdentifiedField,
        date_widget: DateWidgetInfo,
        target_date: date,
        logger: SessionLogger,
    ) -> FillResult:
        start = time.monotonic()
        iso = target_date.isoformat()
        logger.log("filler_started", strategy=self.strategy_name,
                   iso=iso, target=iso)

        await session.page.fill(field.selector, iso)
        await session.wait_ms(500)

        logger.log("date_native_filled", iso=iso)
        return FillResult(
            success=True,
            strategy_used=self.strategy_name,
            target_value=iso,
            matched_option=iso,
            state_changes=None,
            duration_seconds=time.monotonic() - start,
            error=None,
        )
