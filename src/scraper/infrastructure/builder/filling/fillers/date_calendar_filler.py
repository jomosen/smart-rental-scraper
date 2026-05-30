"""Date filler for custom calendar widgets: navigates month-by-month, clicks target day."""
from __future__ import annotations

import re
import time
from datetime import date, datetime

from ...browser_session import BrowserSession
from ...date_analysis.date_widget_classifier import DateWidgetInfo
from ...field_analysis.field_identifier import IdentifiedField
from ..base_filler import DateFiller, FillResult
from ...session_logger import SessionLogger

# Multilingual month-name lookup tables
_ES = {"enero":1,"febrero":2,"marzo":3,"abril":4,"mayo":5,"junio":6,
       "julio":7,"agosto":8,"septiembre":9,"octubre":10,"noviembre":11,"diciembre":12}
_FR = {"janvier":1,"février":2,"mars":3,"avril":4,"mai":5,"juin":6,
       "juillet":7,"août":8,"septembre":9,"octobre":10,"novembre":11,"décembre":12}
_PT = {"janeiro":1,"fevereiro":2,"março":3,"abril":4,"maio":5,"junho":6,
       "julho":7,"agosto":8,"setembro":9,"outubro":10,"novembro":11,"dezembro":12}
_IT = {"gennaio":1,"febbraio":2,"marzo":3,"aprile":4,"maggio":5,"giugno":6,
       "luglio":7,"agosto":8,"settembre":9,"ottobre":10,"novembre":11,"dicembre":12}
_DE = {"januar":1,"februar":2,"märz":3,"april":4,"mai":5,"juni":6,
       "juli":7,"august":8,"september":9,"oktober":10,"november":11,"dezember":12}

_ALL_LOCALE_DICTS = (_ES, _FR, _PT, _IT, _DE)

# Class-name fragments that mark a day cell as non-selectable.
# "neighboringmonth" matches react-calendar's `--neighboringMonth` suffix,
# which appears on overflow days from the adjacent month panel in double-view
# range calendars — clicking them would select the wrong month.
_EXCLUDED_CLASS_KEYWORDS = (
    "disabled", "unavailable", "blocked", "outside", "neighboringmonth",
)


def _is_day_cell_excluded(
    aria_disabled: str, disabled_attr: str | None, class_attr: str
) -> bool:
    """Return True when a day cell must not be clicked."""
    if aria_disabled.lower() == "true":
        return True
    if disabled_attr is not None:
        return True
    class_lower = class_attr.lower()
    return any(kw in class_lower for kw in _EXCLUDED_CLASS_KEYWORDS)


def _parse_month_year(label: str) -> tuple[int, int] | None:
    """Return (year, month) from a calendar header label, or None if unparseable."""
    label = label.strip()
    if not label:
        return None

    # Numeric patterns: YYYY-MM, MM/YYYY, YYYY/MM
    m = re.search(r"(\d{4})[/\-](\d{1,2})", label)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d{1,2})[/\-](\d{4})", label)
    if m:
        return int(m.group(2)), int(m.group(1))

    # English strptime
    for fmt in ("%B %Y", "%b %Y", "%Y %B", "%Y, %B"):
        try:
            dt = datetime.strptime(label, fmt)
            return dt.year, dt.month
        except ValueError:
            pass

    # Multilingual month name dicts
    m_year = re.search(r"(\d{4})", label)
    if m_year:
        year = int(m_year.group(1))
        label_lower = label.lower()
        for locale_dict in _ALL_LOCALE_DICTS:
            for name, num in locale_dict.items():
                if name in label_lower:
                    return year, num

    return None


class DateCalendarFiller(DateFiller):
    """
    Navigates a custom calendar to the target month and clicks the target day.
    All selectors come from DateWidgetInfo — nothing hardcoded.

    If the calendar is already open (e.g. range calendar after pickup fill),
    the open-click step is skipped automatically.
    """

    strategy_name = "date_calendar"
    MAX_MONTH_NAVIGATIONS = 14

    async def fill(
        self,
        session: BrowserSession,
        field: IdentifiedField,
        date_widget: DateWidgetInfo,
        target_date: date,
        logger: SessionLogger,
    ) -> FillResult:
        start = time.monotonic()
        log_dir = logger.log_dir

        def _fail(msg: str) -> FillResult:
            logger.log("filler_failed", error=msg, strategy=self.strategy_name)
            return FillResult(
                success=False,
                strategy_used=self.strategy_name,
                target_value=target_date.isoformat(),
                matched_option=None,
                state_changes=None,
                duration_seconds=time.monotonic() - start,
                error=msg,
            )

        logger.log("filler_started", strategy=self.strategy_name,
                   target=target_date.isoformat())

        # ── Step 1: open calendar if not already visible ──────────────────────
        container_sel = date_widget.calendar_container_selector
        calendar_visible = False
        if container_sel:
            calendar_visible = await session.is_visible(container_sel, "css")

        if calendar_visible:
            logger.log("date_calendar_already_open", container=container_sel)
        else:
            clicked = await session.click_selector(field.selector, field.selector_type)
            if not clicked:
                return _fail(f"Could not open calendar by clicking {field.selector!r}")
            await session.wait_ms(800)
            logger.log("date_calendar_opened", selector=field.selector)

        # ── Step 2: navigate to the target month ──────────────────────────────
        next_sel = date_widget.next_month_selector
        prev_sel = date_widget.prev_month_selector
        label_sel = date_widget.month_year_label_selector

        if not next_sel:
            return _fail("next_month_selector is None — cannot navigate calendar")

        prev_label: str | None = None

        for nav_step in range(self.MAX_MONTH_NAVIGATIONS):
            # Read current month label
            label_text = ""
            if label_sel:
                try:
                    label_text = await (
                        session.page.locator(label_sel).first
                        .inner_text(timeout=2_000)
                    )
                    label_text = label_text.strip()
                except Exception:
                    pass

            current = _parse_month_year(label_text) if label_text else None
            logger.log("date_calendar_month_read",
                       label=label_text, parsed=current, nav_step=nav_step)

            if current is not None:
                cur_year, cur_month = current
                tgt_year, tgt_month = target_date.year, target_date.month

                if (cur_year, cur_month) == (tgt_year, tgt_month):
                    logger.log("date_calendar_month_reached",
                               year=tgt_year, month=tgt_month)
                    break

                # Progress check (only when we have readable labels)
                if label_text and label_text == prev_label:
                    return _fail(
                        f"Calendar stuck at {label_text!r} — "
                        f"navigation button did not advance the month."
                    )
                prev_label = label_text

                # Choose direction
                if (cur_year, cur_month) < (tgt_year, tgt_month):
                    nav_sel, direction = next_sel, "next"
                else:
                    nav_sel = prev_sel or next_sel
                    direction = "prev"

                logger.log("date_calendar_month_nav",
                           from_month=label_text, direction=direction,
                           nav_step=nav_step)

                clicked_nav = await session.click_selector(nav_sel, "css")
                if not clicked_nav:
                    return _fail(
                        f"Could not click {direction}_month button: {nav_sel!r}"
                    )
                await session.wait_ms(400)

                # Save per-step snapshot (calendar container HTML)
                try:
                    snap_html = (
                        await session.get_inner_html(container_sel)
                        if container_sel
                        else await session.get_html()
                    )
                    (log_dir / "dom_snapshots" / f"cal_nav_{nav_step:02d}.html").write_text(
                        snap_html, encoding="utf-8"
                    )
                except Exception:
                    pass

            else:
                # Label unreadable — attempt day click with no navigation
                logger.log("date_calendar_month_parse_failed",
                           label=label_text,
                           fallback="attempt_day_click_without_nav")
                break

        else:
            return _fail(
                f"Calendar navigation limit ({self.MAX_MONTH_NAVIGATIONS}) reached "
                f"without landing on {target_date.year}-{target_date.month:02d}."
            )

        # ── Step 3: click the target day ──────────────────────────────────────
        day_sel = date_widget.day_cell_selector
        if not day_sel:
            return _fail("day_cell_selector is None — cannot select day")

        day_str = str(target_date.day)
        day_pattern = re.compile(rf"^\s*{day_str}\s*$")

        if container_sel:
            day_locator = (
                session.page.locator(container_sel)
                .locator(day_sel)
                .filter(has_text=day_pattern)
            )
        else:
            day_locator = session.page.locator(day_sel).filter(has_text=day_pattern)

        count = await day_locator.count()
        if count == 0:
            return _fail(
                f"No day cell found for day={day_str!r} using "
                f"day_cell_selector={day_sel!r}"
            )

        # Try each matching cell, skip disabled ones
        clicked_day = False
        for i in range(count):
            cell = day_locator.nth(i)

            aria_disabled = (await cell.get_attribute("aria-disabled")) or ""
            disabled_attr = await cell.get_attribute("disabled")
            class_attr = (await cell.get_attribute("class")) or ""

            is_disabled = _is_day_cell_excluded(aria_disabled, disabled_attr, class_attr)
            if is_disabled:
                logger.log("date_calendar_day_skipped",
                           day=day_str, index=i, reason="disabled",
                           class_attr=class_attr[:80])
                continue

            try:
                await cell.wait_for(state="visible", timeout=2_000)
                await cell.click(timeout=2_000)
                clicked_day = True
                logger.log("date_calendar_day_clicked", day=day_str, index=i)
                break
            except Exception as exc:
                logger.log("date_calendar_day_click_error",
                           day=day_str, index=i, error=str(exc))

        if not clicked_day:
            return _fail(
                f"All {count} cell(s) for day={day_str!r} were disabled "
                f"or unclickable."
            )

        await session.wait_ms(800)

        return FillResult(
            success=True,
            strategy_used=self.strategy_name,
            target_value=target_date.isoformat(),
            matched_option=day_str,
            state_changes=None,
            duration_seconds=time.monotonic() - start,
            error=None,
        )
