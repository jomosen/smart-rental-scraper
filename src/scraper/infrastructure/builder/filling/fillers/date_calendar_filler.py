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

# Exact class TOKENS (not substrings — "off" must not match "offer") that mark
# adjacent-month overflow days: daterangepicker uses "off", bootstrap-datepicker
# uses "old"/"new". Such cells can carry "available" too, and clicking them
# silently selects a day of the WRONG month.
_EXCLUDED_CLASS_TOKENS = frozenset({"off", "old", "new"})

# Walk up from a day cell to the smallest ancestor containing exactly ONE
# month label — that label names the cell's month panel. Returns the label
# text, or null when it cannot be determined (single-panel widgets whose
# label sits outside the walked ancestors, ambiguous containers, etc.).
_CELL_MONTH_JS = """
(el, labelSel) => {
    let node = el.parentElement;
    while (node && node !== document.documentElement) {
        let labels;
        try { labels = node.querySelectorAll(labelSel); } catch (e) { return null; }
        if (labels.length === 1) return labels[0].textContent;
        if (labels.length > 1) return null;
        node = node.parentElement;
    }
    return null;
}
"""


def _is_day_cell_excluded(
    aria_disabled: str, disabled_attr: str | None, class_attr: str
) -> bool:
    """Return True when a day cell must not be clicked."""
    if aria_disabled.lower() == "true":
        return True
    if disabled_attr is not None:
        return True
    class_lower = class_attr.lower()
    if any(kw in class_lower for kw in _EXCLUDED_CLASS_KEYWORDS):
        return True
    return any(t in _EXCLUDED_CLASS_TOKENS for t in class_lower.split())


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


def _parse_date_value(value: str | None, fmt_hint: str | None) -> tuple[int, int, int] | None:
    """Parse a field's displayed value into (year, month, day), or None.

    Handles DD/MM/YYYY-style (day-first unless fmt_hint starts with MM) and
    ISO YYYY-MM-DD. Used to VERIFY what a day click actually selected.
    """
    if not value:
        return None
    m = re.search(r"(\d{4})[/.\-](\d{1,2})[/.\-](\d{1,2})", value)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    m = re.search(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})", value)
    if m:
        a, b, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if year < 100:
            year += 2000
        if (fmt_hint or "").upper().startswith("MM"):
            month, day = a, b
        else:
            day, month = a, b
        return year, month, day
    return None


class DateCalendarFiller(DateFiller):
    """
    Navigates a custom calendar to the target month and clicks the target day.
    All selectors come from DateWidgetInfo — nothing hardcoded.

    If the calendar is already open (e.g. range calendar after pickup fill),
    the open-click step is skipped automatically.

    Hostile widgets (multiple picker instances, sticky "open" CSS classes,
    same day number in several visible panels) make selector-based targeting
    unreliable, so after clicking a day the filler VERIFIES the field's own
    value: if it changed to a different date than the target, the click hit
    the wrong cell and the next candidate is tried. A value that does not
    change (widgets that only sync the input when the range completes) is
    accepted as before.
    """

    strategy_name = "date_calendar"
    MAX_MONTH_NAVIGATIONS = 14
    MAX_FILL_PASSES = 3

    async def fill(
        self,
        session: BrowserSession,
        field: IdentifiedField,
        date_widget: DateWidgetInfo,
        target_date: date,
        logger: SessionLogger,
    ) -> FillResult:
        start = time.monotonic()

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

        # ── Steps 2+3: navigate + click, with post-click value verification ───
        last_error = "day click failed"
        value_reactive = False  # proven: this widget updates the input per click
        for fill_pass in range(self.MAX_FILL_PASSES):
            # Pages can hold SEVERAL picker instances matching the container
            # selector (stale ones keep their "open" CSS class and even stay
            # is_visible()). Resolve the instance that is actually on top and
            # confine every lookup (labels, arrows, day cells) to it.
            active = await self._resolve_active_container(session, container_sel)

            nav_error = await self._navigate_to_month(
                session, date_widget, target_date, logger, active
            )
            if nav_error:
                return _fail(nav_error)

            outcome, err, saw_reactive = await self._click_target_day(
                session, field, date_widget, target_date, logger, active,
                value_reactive,
            )
            value_reactive = value_reactive or saw_reactive
            if outcome == "ok":
                await session.wait_ms(800)
                return FillResult(
                    success=True,
                    strategy_used=self.strategy_name,
                    target_value=target_date.isoformat(),
                    matched_option=str(target_date.day),
                    state_changes=None,
                    duration_seconds=time.monotonic() - start,
                    error=None,
                )
            last_error = err or last_error
            if outcome == "failed":
                return _fail(last_error)
            # outcome == "retry": a wrong-value click was undone by reopening
            # the calendar — re-navigate (the view may have moved) and rescan.
            logger.log("date_calendar_fill_retry_pass", next_pass=fill_pass + 1)

        return _fail(last_error)

    # ── Step 2: navigate to the target month ──────────────────────────────────

    async def _navigate_to_month(
        self,
        session: BrowserSession,
        date_widget: DateWidgetInfo,
        target_date: date,
        logger: SessionLogger,
        active=None,
    ) -> str | None:
        """Bring the target month on screen. Returns an error string, or None."""
        next_sel = date_widget.next_month_selector
        prev_sel = date_widget.prev_month_selector
        label_sel = date_widget.month_year_label_selector
        container_sel = date_widget.calendar_container_selector
        log_dir = logger.log_dir

        if not next_sel:
            return "next_month_selector is None — cannot navigate calendar"

        prev_label: str | None = None

        for nav_step in range(self.MAX_MONTH_NAVIGATIONS):
            # Read ALL visible month labels. Range calendars render several
            # months side by side — the target may already be on screen even
            # when the FIRST label is a different month. Hidden/stale picker
            # instances are skipped.
            label_texts: list[str] = []
            if label_sel:
                try:
                    label_loc = session.page.locator(label_sel)
                    for i in await self._within(label_loc, active):
                        el = label_loc.nth(i)
                        if not await el.is_visible():
                            continue
                        text = (await el.inner_text()).strip()
                        if text:
                            label_texts.append(text)
                except Exception:
                    pass
            label_text = label_texts[0] if label_texts else ""

            visible_months = [
                p for p in (_parse_month_year(t) for t in label_texts) if p
            ]
            current = visible_months[0] if visible_months else None
            logger.log("date_calendar_month_read",
                       label=label_text, parsed=current,
                       visible=visible_months, nav_step=nav_step)

            if current is None:
                # Label unreadable — attempt day click with no navigation
                logger.log("date_calendar_month_parse_failed",
                           label=label_text,
                           fallback="attempt_day_click_without_nav")
                return None

            tgt = (target_date.year, target_date.month)
            if tgt in visible_months:
                # Let the grid catch up with the header: after rapid month
                # navigation some widgets update the label a beat before the
                # day cells — clicking too early selects a day of the PREVIOUS
                # month while every check reads the new one.
                if nav_step > 0:
                    await session.wait_ms(700)
                logger.log("date_calendar_month_reached",
                           year=tgt[0], month=tgt[1])
                return None

            # Progress check (only when we have readable labels)
            if label_text and label_text == prev_label:
                return (f"Calendar stuck at {label_text!r} — "
                        f"navigation button did not advance the month.")
            prev_label = label_text

            # Choose direction relative to the whole visible window
            if tgt > max(visible_months):
                nav_sel, direction = next_sel, "next"
            else:
                nav_sel = prev_sel or next_sel
                direction = "prev"

            logger.log("date_calendar_month_nav",
                       from_month=label_text, direction=direction,
                       nav_step=nav_step)

            # The selector can match arrows in stale/hidden picker instances
            # too — click the first VISIBLE one inside the active instance.
            clicked_nav = False
            nav_locator = session.page.locator(nav_sel)
            for i in await self._within(nav_locator, active):
                cand = nav_locator.nth(i)
                try:
                    if await cand.is_visible():
                        await cand.click(timeout=5_000)
                        clicked_nav = True
                        break
                except Exception:
                    continue
            if not clicked_nav:
                return f"Could not click {direction}_month button: {nav_sel!r}"
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

        return (f"Calendar navigation limit ({self.MAX_MONTH_NAVIGATIONS}) reached "
                f"without landing on {target_date.year}-{target_date.month:02d}.")

    # ── Step 3: click the target day (value-verified) ─────────────────────────

    async def _click_target_day(
        self,
        session: BrowserSession,
        field: IdentifiedField,
        date_widget: DateWidgetInfo,
        target_date: date,
        logger: SessionLogger,
        active=None,
        value_reactive: bool = False,
    ) -> tuple[str, str | None, bool]:
        """Click the target day and verify the field's value actually changed
        to the target date. Returns (outcome, error, saw_reactive):
          ("ok", None, _)      — clicked, and value confirms it (or is unverifiable)
          ("retry", error, _)  — wrong-value click; calendar reopened, re-nav needed
          ("failed", error, _) — nothing clickable / no candidates

        value_reactive: the widget has PROVEN (this fill) that it updates the
        field value on every click — an unchanged value then means the clicked
        cell was inert (stale picker instance), not "widget syncs late", so
        the scan moves on instead of accepting it.
        """
        container_sel = date_widget.calendar_container_selector
        label_sel = date_widget.month_year_label_selector
        day_sel = date_widget.day_cell_selector

        day_str = str(target_date.day)
        day_pattern = re.compile(rf"^\s*{day_str}\s*$")

        if not day_sel:
            return "failed", "day_cell_selector is None — cannot select day", False

        if container_sel:
            day_locator = (
                session.page.locator(container_sel)
                .locator(day_sel)
                .filter(has_text=day_pattern)
            )
        else:
            day_locator = session.page.locator(day_sel).filter(has_text=day_pattern)

        count = await day_locator.count()
        if count == 0 and container_sel:
            # The classifier sometimes returns day_cell_selector already
            # prefixed with the container ("div.picker … td.day"); nesting that
            # inside the container again matches nothing. Retry unscoped.
            day_locator = session.page.locator(day_sel).filter(has_text=day_pattern)
            count = await day_locator.count()
            if count:
                logger.log("date_calendar_day_unscoped_retry",
                           day=day_str, count=count)
        if count == 0:
            return "failed", (f"No day cell found for day={day_str!r} using "
                              f"day_cell_selector={day_sel!r}"), False

        target_ymd = (target_date.year, target_date.month, target_date.day)
        pre_value = await self._read_field_value(session, field)

        indices = await self._within(day_locator, active)
        if not indices:
            indices = list(range(count))  # active scoping matched nothing — page-wide

        for i in indices:
            cell = day_locator.nth(i)

            aria_disabled = (await cell.get_attribute("aria-disabled")) or ""
            disabled_attr = await cell.get_attribute("disabled")
            class_attr = (await cell.get_attribute("class")) or ""

            if _is_day_cell_excluded(aria_disabled, disabled_attr, class_attr):
                logger.log("date_calendar_day_skipped",
                           day=day_str, index=i, reason="disabled",
                           class_attr=class_attr[:80])
                continue

            # With several candidates (multi-month range calendars repeat the
            # same day number in every panel, stale picker instances add
            # more), skip cells whose month panel is NOT the target month.
            if count > 1 and label_sel:
                try:
                    panel_label = await cell.evaluate(_CELL_MONTH_JS, label_sel)
                except Exception:
                    panel_label = None
                panel_month = _parse_month_year(panel_label) if panel_label else None
                if panel_month is not None and panel_month != (
                    target_date.year, target_date.month
                ):
                    logger.log("date_calendar_day_skipped",
                               day=day_str, index=i, reason="wrong_month_panel",
                               panel=panel_label.strip())
                    continue

            try:
                await cell.wait_for(state="visible", timeout=2_000)
                await cell.click(timeout=2_000)
            except Exception as exc:
                logger.log("date_calendar_day_click_error",
                           day=day_str, index=i, error=str(exc))
                continue

            # ── Verify what the click actually selected ───────────────────────
            await session.wait_ms(300)
            post_value = await self._read_field_value(session, field)
            parsed = _parse_date_value(post_value, date_widget.date_format)

            if parsed == target_ymd and parsed is not None:
                logger.log("date_calendar_day_clicked", day=day_str, index=i,
                           verified=True)
                return "ok", None, post_value != pre_value
            if post_value == pre_value or parsed is None:
                if value_reactive:
                    # The widget provably updates the value on real clicks, so
                    # an unchanged value has two possible causes:
                    #   a) inert cell (stale picker instance), or
                    #   b) a RANGE picker mid-selection: our click just set
                    #      the range END, and a SECOND click on the same cell
                    #      starts a new range at it — committing the value.
                    # Try (b) once before writing the cell off as (a).
                    try:
                        await cell.click(timeout=2_000)
                        await session.wait_ms(300)
                        post2 = await self._read_field_value(session, field)
                        parsed2 = _parse_date_value(post2, date_widget.date_format)
                        if parsed2 == target_ymd and parsed2 is not None:
                            logger.log("date_calendar_day_clicked",
                                       day=day_str, index=i,
                                       verified=True, second_click=True)
                            return "ok", None, True
                        if post2 != post_value and parsed2 is not None:
                            logger.log("date_calendar_day_wrong_value",
                                       day=day_str, index=i, got=post2,
                                       expected=target_date.isoformat(),
                                       second_click=True)
                            await session.click_selector(
                                field.selector, field.selector_type)
                            await session.wait_ms(800)
                            return "retry", (
                                f"Second click for day={day_str!r} selected "
                                f"{post2!r} instead of {target_date.isoformat()}"
                            ), True
                    except Exception:
                        pass
                    logger.log("date_calendar_day_skipped",
                               day=day_str, index=i,
                               reason="inert_cell_no_value_change")
                    continue
                # Widget doesn't sync the input per click (or value is
                # unreadable) — accept the click, as before verification.
                logger.log("date_calendar_day_clicked", day=day_str, index=i,
                           verified=False)
                return "ok", None, False

            # Wrong cell: the click selected a different date (same day
            # number, wrong month panel / stale instance). Reopen the
            # calendar and let the caller re-resolve the active instance,
            # re-navigate and rescan.
            logger.log("date_calendar_day_wrong_value",
                       day=day_str, index=i, got=post_value,
                       expected=target_date.isoformat())
            await session.click_selector(field.selector, field.selector_type)
            await session.wait_ms(800)
            return "retry", (f"Click for day={day_str!r} selected {post_value!r} "
                             f"instead of {target_date.isoformat()}"), True

        return "failed", (f"All {count} cell(s) for day={day_str!r} were disabled, "
                          f"inert or unclickable."), False

    @staticmethod
    async def _resolve_active_container(session: BrowserSession, container_sel: str | None):
        """Among all instances matching container_sel, return an ElementHandle
        for the one actually interactive (topmost at its own center), or None.

        Needed because some sites keep several picker instances in the DOM and
        stale ones retain their "open" CSS class — selector scoping alone
        cannot tell them apart.
        """
        if not container_sel:
            return None
        try:
            loc = session.page.locator(container_sel)
            best = None
            for i in range(await loc.count()):
                inst = loc.nth(i)
                try:
                    if not await inst.is_visible():
                        continue
                    on_top = await inst.evaluate(
                        """el => {
                            const r = el.getBoundingClientRect();
                            if (!r.width || !r.height) return false;
                            const p = document.elementFromPoint(
                                r.left + r.width / 2,
                                r.top + Math.min(20, r.height / 2));
                            return !!p && (el === p || el.contains(p));
                        }"""
                    )
                except Exception:
                    continue
                if on_top:
                    best = inst  # later instances stack on top — keep the last
            return await best.element_handle() if best is not None else None
        except Exception:
            return None

    @staticmethod
    async def _within(locator, active) -> list[int]:
        """Indices of locator matches, ORDERED with those contained in
        *active* first (identity order when active is None).

        Preference, not a hard filter: if the active-instance resolution
        picked the wrong picker, the true cells are still reachable later in
        the scan — value verification decides which click actually counts.
        """
        n = await locator.count()
        if active is None:
            return list(range(n))
        inside: list[int] = []
        outside: list[int] = []
        for i in range(n):
            try:
                contained = await locator.nth(i).evaluate(
                    "(el, root) => root.contains(el)", active
                )
            except Exception:
                contained = False
            (inside if contained else outside).append(i)
        return inside + outside

    @staticmethod
    async def _read_field_value(session: BrowserSession, field: IdentifiedField) -> str | None:
        """Best-effort read of the field's current displayed value."""
        try:
            loc = session.page.locator(field.selector).first
        except Exception:
            return None
        try:
            return await loc.input_value(timeout=1_000)
        except Exception:
            pass
        try:
            return (await loc.inner_text(timeout=500)).strip()
        except Exception:
            return None
