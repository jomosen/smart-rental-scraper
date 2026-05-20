"""Orchestrator for Experiment 4: fill pickup_date + return_date."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from anthropic import AsyncAnthropic
from bs4 import BeautifulSoup

from browser_session import BrowserSession
from cookie_closer import close_cookies_in_session
from date_analysis.date_widget_classifier import DateWidgetInfo, classify_date_widget
from field_analysis.field_identifier import FormFields, identify_form_fields
from filling.base_filler import FillResult
from filling.date_filler_factory import UnsupportedDateWidgetError, get_date_filler
from filling.fillers.date_calendar_filler import DateCalendarFiller
from filling.form_state import (
    FormStateDiff,
    capture_form_state,
    compare_states,
    save_snapshot as save_state_snapshot,
)
from form_capture.form_capturer import capture_search_form
from form_capture.html_cleaner import clean_html_for_llm
from location_explorer import create_log_dir
from session_logger import SessionLogger


# ── Log helpers ───────────────────────────────────────────────────────────────

from datetime import datetime as _dt, timezone as _tz

def _now() -> str:
    return _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _log(log_dir: Path, event_type: str, **kwargs) -> None:
    event = {"ts": _now(), "type": event_type, **kwargs}
    with open(log_dir / "trace.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _snap(log_dir: Path, filename: str, content: str) -> None:
    (log_dir / "dom_snapshots" / filename).write_text(content, encoding="utf-8")


def _extract_element_html(html: str, selector: str, selector_type: str) -> str:
    if selector_type == "css":
        try:
            soup = BeautifulSoup(html, "html.parser")
            elements = soup.select(selector)
            if elements:
                return str(elements[0])
        except Exception:
            pass
    return html


async def _detect_form_selector(session: BrowserSession) -> str:
    selector: str = await session.page.evaluate("""
    () => {
        const forms = document.querySelectorAll("form");
        if (!forms.length) return "form";
        let best = null, bestCount = 0;
        for (const f of forms) {
            const n = f.querySelectorAll("input, select, textarea").length;
            if (n > bestCount) { bestCount = n; best = f; }
        }
        if (!best) return "form";
        return best.id ? "#" + best.id : "form";
    }
    """)
    return selector or "form"


# ── Report dataclass ──────────────────────────────────────────────────────────

@dataclass
class DateFillExperimentReport:
    site_url: str
    pickup_target: date
    return_target: date
    success: bool
    pickup_result: FillResult | None
    pickup_diff: FormStateDiff | None
    return_result: FillResult | None
    return_diff: FormStateDiff | None
    duration_seconds: float
    cost_estimate_eur: float
    error: str | None


# ── Main orchestrator ─────────────────────────────────────────────────────────

async def fill_dates_experiment(
    url: str,
    pickup_target: date,
    return_target: date,
    log_dir: Path,
    headless: bool = True,
) -> DateFillExperimentReport:
    """
    Full flow:
    1. Navigate + close cookies + identify fields.
    2. Click pickup_date → classify date widget → fill pickup → diff.
    3. If is_range_calendar and calendar still open, reuse it for return;
       otherwise click return_date → classify → fill return → diff.
    4. success = both diffs have_changes.
    """
    t0 = time.monotonic()
    total_cost = 0.0
    client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    def _fail(
        msg: str,
        pickup_result: FillResult | None = None,
        pickup_diff: FormStateDiff | None = None,
    ) -> DateFillExperimentReport:
        _log(log_dir, "result", success=False, error=msg)
        return DateFillExperimentReport(
            site_url=url,
            pickup_target=pickup_target,
            return_target=return_target,
            success=False,
            pickup_result=pickup_result,
            pickup_diff=pickup_diff,
            return_result=None,
            return_diff=None,
            duration_seconds=time.monotonic() - t0,
            cost_estimate_eur=total_cost,
            error=msg,
        )

    async with BrowserSession(headless=headless) as session:
        # ── 1. Navigate ───────────────────────────────────────────────────────
        _log(log_dir, "navigate", url=url)
        nav_t0 = time.monotonic()
        await session.navigate(url)
        _log(log_dir, "navigate_complete",
             url=url, duration_ms=int((time.monotonic() - nav_t0) * 1000))

        # ── 2. Close cookies ──────────────────────────────────────────────────
        cookie_result = await close_cookies_in_session(session, log_dir)
        total_cost += cookie_result.cost_estimate_eur
        _log(log_dir, "cookie_closer_invoked",
             success=cookie_result.success, cost_eur=cookie_result.cost_estimate_eur)

        # ── 3. Capture form + identify fields ─────────────────────────────────
        form_result = await capture_search_form(session, log_dir)
        if form_result is None:
            return _fail("Form not found in page HTML")

        raw_form, cleaned_form = form_result
        raw_size = len(raw_form.encode("utf-8"))
        clean_size = len(cleaned_form.encode("utf-8"))
        _log(log_dir, "form_located", raw_bytes=raw_size, cleaned_bytes=clean_size,
             reduction_pct=round(100 * (1 - clean_size / raw_size), 1))

        try:
            fields, field_cost = await identify_form_fields(cleaned_form, log_dir, client)
        except Exception as exc:
            return _fail(f"field_identification failed: {exc}")

        total_cost += field_cost
        _log(log_dir, "fields_identified",
             pickup_date=fields.pickup_date.selector if fields.pickup_date else None,
             return_date=fields.return_date.selector if fields.return_date else None)

        if fields.pickup_date is None:
            return _fail("pickup_date field not identified")

        form_selector = await _detect_form_selector(session)
        _log(log_dir, "form_selector_detected", selector=form_selector)

        # ── 4. PICKUP DATE ────────────────────────────────────────────────────
        pickup_field = fields.pickup_date

        # Click to open widget
        _log(log_dir, "date_pickup_field_clicked",
             selector=pickup_field.selector, type=pickup_field.element_kind)
        await session.click_selector(pickup_field.selector, pickup_field.selector_type)
        await session.wait_ms(800)

        html_after_pickup_click = await session.get_html()
        _snap(log_dir, "date_pickup_after_click.html", html_after_pickup_click)
        cleaned_after_pickup = clean_html_for_llm(html_after_pickup_click)

        # Classify date widget
        field_html_before = _extract_element_html(
            cleaned_form, pickup_field.selector, pickup_field.selector_type
        )
        try:
            pickup_date_widget, dw_cost = await classify_date_widget(
                field_html_before, cleaned_after_pickup,
                label="date_pickup_widget", log_dir=log_dir, client=client,
            )
        except Exception as exc:
            return _fail(f"pickup date widget classification failed: {exc}")

        total_cost += dw_cost
        _log(log_dir, "date_pickup_widget_classified",
             widget_type=pickup_date_widget.widget_type,
             accepts_direct_typing=pickup_date_widget.accepts_direct_typing,
             date_format=pickup_date_widget.date_format,
             is_range_calendar=pickup_date_widget.is_range_calendar)

        try:
            pickup_filler = get_date_filler(pickup_date_widget)
        except UnsupportedDateWidgetError as exc:
            return _fail(str(exc))

        _log(log_dir, "pickup_filler_selected", strategy=pickup_filler.strategy_name)

        # Capture state BEFORE pickup fill
        state_before_pickup = await capture_form_state(session, form_selector)
        save_state_snapshot(log_dir, "date_pickup_state_before.json", state_before_pickup)

        # Fill pickup
        filler_logger = SessionLogger(log_dir)
        pickup_result = await pickup_filler.fill(
            session, pickup_field, pickup_date_widget, pickup_target, filler_logger
        )

        # Capture state AFTER and diff
        state_after_pickup = await capture_form_state(session, form_selector)
        save_state_snapshot(log_dir, "date_pickup_state_after.json", state_after_pickup)
        pickup_diff = compare_states(state_before_pickup, state_after_pickup)
        save_state_snapshot(log_dir, "date_pickup_state_diff.json", pickup_diff)
        _log(log_dir, "date_pickup_diff",
             has_changes=pickup_diff.has_changes,
             changed_count=len(pickup_diff.changed_inputs))

        if not pickup_result.success:
            return _fail(f"Pickup fill failed: {pickup_result.error}",
                         pickup_result=pickup_result, pickup_diff=pickup_diff)

        # ── 5. RETURN DATE ────────────────────────────────────────────────────
        return_field = fields.return_date
        return_date_widget = pickup_date_widget  # default: reuse if range calendar

        # Check if the calendar from pickup is still open (range calendar)
        reuse_range_calendar = False
        if (pickup_date_widget.is_range_calendar
                and pickup_date_widget.calendar_container_selector):
            reuse_range_calendar = await session.is_visible(
                pickup_date_widget.calendar_container_selector, "css"
            )

        if reuse_range_calendar:
            _log(log_dir, "date_return_reused_range_calendar",
                 container=pickup_date_widget.calendar_container_selector)
            # Use same widget info; DateCalendarFiller will detect calendar is open
            return_filler = DateCalendarFiller()
            # Use return_field if identified, else fall back to pickup_field
            effective_return_field = return_field or pickup_field
        else:
            if return_field is None:
                return _fail(
                    "return_date field not identified and range calendar not available",
                    pickup_result=pickup_result, pickup_diff=pickup_diff,
                )

            # Click to open return widget
            _log(log_dir, "date_return_field_clicked", selector=return_field.selector)
            await session.click_selector(return_field.selector, return_field.selector_type)
            await session.wait_ms(800)

            html_after_return_click = await session.get_html()
            _snap(log_dir, "date_return_after_click.html", html_after_return_click)
            cleaned_after_return = clean_html_for_llm(html_after_return_click)

            field_html_before_return = _extract_element_html(
                cleaned_form, return_field.selector, return_field.selector_type
            )
            try:
                return_date_widget, dw_cost2 = await classify_date_widget(
                    field_html_before_return, cleaned_after_return,
                    label="date_return_widget", log_dir=log_dir, client=client,
                )
            except Exception as exc:
                return _fail(f"return date widget classification failed: {exc}",
                             pickup_result=pickup_result, pickup_diff=pickup_diff)

            total_cost += dw_cost2
            _log(log_dir, "date_return_widget_classified",
                 widget_type=return_date_widget.widget_type,
                 accepts_direct_typing=return_date_widget.accepts_direct_typing)

            try:
                return_filler = get_date_filler(return_date_widget)
            except UnsupportedDateWidgetError as exc:
                return _fail(str(exc), pickup_result=pickup_result,
                             pickup_diff=pickup_diff)

            effective_return_field = return_field

        _log(log_dir, "return_filler_selected", strategy=return_filler.strategy_name)

        # Capture state BEFORE return fill
        state_before_return = await capture_form_state(session, form_selector)
        save_state_snapshot(log_dir, "date_return_state_before.json", state_before_return)

        return_result = await return_filler.fill(
            session, effective_return_field, return_date_widget, return_target, filler_logger
        )

        state_after_return = await capture_form_state(session, form_selector)
        save_state_snapshot(log_dir, "date_return_state_after.json", state_after_return)
        return_diff = compare_states(state_before_return, state_after_return)
        save_state_snapshot(log_dir, "date_return_state_diff.json", return_diff)
        _log(log_dir, "date_return_diff",
             has_changes=return_diff.has_changes,
             changed_count=len(return_diff.changed_inputs))

        success = pickup_diff.has_changes and return_diff.has_changes and return_result.success
        error = None if success else (
            return_result.error or "One or both diffs reported no changes"
        )

        _log(log_dir, "result",
             success=success,
             pickup_has_changes=pickup_diff.has_changes,
             return_has_changes=return_diff.has_changes,
             cost_eur=total_cost)

        return DateFillExperimentReport(
            site_url=url,
            pickup_target=pickup_target,
            return_target=return_target,
            success=success,
            pickup_result=pickup_result,
            pickup_diff=pickup_diff,
            return_result=return_result,
            return_diff=return_diff,
            duration_seconds=time.monotonic() - t0,
            cost_estimate_eur=total_cost,
            error=error,
        )
