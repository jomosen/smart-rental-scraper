"""Orchestrator for Experiment 5: fill pickup_time + dropoff_time."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from anthropic import AsyncAnthropic
from bs4 import BeautifulSoup

from .browser_session import BrowserSession
from .cookie_closer import close_cookies_in_session
from .field_analysis.field_identifier import identify_form_fields
from .field_analysis.widget_classifier import classify_widget
from .field_analysis.widget_opener import open_widget_reliably
from .filling.base_filler import FillResult
from .filling.filler_factory import UnsupportedWidgetError, get_filler_for_widget
from .filling.form_state import is_field_at_target_time
from .form_capture.form_capturer import capture_search_form
from .form_capture.html_cleaner import clean_html_for_llm
from .session_logger import SessionLogger


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
class TimeFillExperimentReport:
    site_url: str
    pickup_time_target: str
    dropoff_time_target: str
    success: bool
    pickup_result: FillResult | None
    dropoff_result: FillResult | None
    duration_seconds: float
    cost_estimate_eur: float
    error: str | None


# ── Main orchestrator ─────────────────────────────────────────────────────────

async def fill_times_experiment(
    url: str,
    pickup_time: str,
    dropoff_time: str,
    log_dir: Path,
    headless: bool = True,
) -> TimeFillExperimentReport:
    """
    Full flow:
    1. Navigate + close cookies + identify fields.
    2. Click pickup_time -> classify widget -> fill (exact match) -> verify.
    3. Click dropoff_time -> classify widget -> fill (exact match) -> verify.
    4. success = both fields verified in final state via normalize_time comparison.
    """
    t0 = time.monotonic()
    total_cost = 0.0
    client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    def _fail(
        msg: str,
        pickup_result: FillResult | None = None,
    ) -> TimeFillExperimentReport:
        _log(log_dir, "result", success=False, error=msg)
        return TimeFillExperimentReport(
            site_url=url,
            pickup_time_target=pickup_time,
            dropoff_time_target=dropoff_time,
            success=False,
            pickup_result=pickup_result,
            dropoff_result=None,
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
             pickup_time=fields.pickup_time.selector if fields.pickup_time else None,
             dropoff_time=fields.return_time.selector if fields.return_time else None)

        if fields.pickup_time is None:
            return _fail("pickup_time field not identified")
        if fields.return_time is None:
            return _fail("return_time (dropoff_time) field not identified")

        form_selector = await _detect_form_selector(session)
        filler_logger = SessionLogger(log_dir)

        # ── 4. PICKUP TIME ────────────────────────────────────────────────────
        pickup_field = fields.pickup_time

        _log(log_dir, "time_pickup_field_opening",
             selector=pickup_field.selector, type=pickup_field.element_kind)
        open_result_pickup = await open_widget_reliably(
            session, pickup_field, filler_logger, label="time_pickup"
        )
        _snap(log_dir, "time_pickup_after_click.html", open_result_pickup.html_after)
        _log(log_dir, "time_pickup_field_opened",
             opened=open_result_pickup.opened, method=open_result_pickup.method,
             options=open_result_pickup.options_detected)

        if not open_result_pickup.opened:
            return _fail(f"Could not open pickup_time widget: {open_result_pickup.error}")

        cleaned_after_pickup = clean_html_for_llm(open_result_pickup.html_after)

        field_html_before_pickup = _extract_element_html(
            cleaned_form, pickup_field.selector, pickup_field.selector_type
        )
        try:
            pickup_widget, wc_cost1 = await classify_widget(
                field_html_before_pickup, cleaned_after_pickup,
                log_dir, client, label="time_pickup_widget",
            )
        except Exception as exc:
            return _fail(f"pickup_time widget classification failed: {exc}")

        total_cost += wc_cost1
        _log(log_dir, "time_pickup_widget_classified",
             widget_type=pickup_widget.widget_type,
             is_searchable=pickup_widget.is_searchable)

        try:
            pickup_filler = get_filler_for_widget(pickup_widget, match_mode="exact")
        except UnsupportedWidgetError as exc:
            return _fail(str(exc))

        pickup_result = await pickup_filler.fill(
            session, pickup_field, pickup_widget, pickup_time, form_selector, filler_logger
        )
        _log(log_dir, "time_pickup_filled",
             success=pickup_result.success, matched=pickup_result.matched_option)

        if not pickup_result.success:
            return _fail(f"Pickup time fill failed: {pickup_result.error}",
                         pickup_result=pickup_result)

        pickup_verified = await is_field_at_target_time(session, pickup_field, pickup_time)
        _log(log_dir, "time_pickup_verified", ok=pickup_verified, target=pickup_time)

        # ── 5. DROPOFF TIME ───────────────────────────────────────────────────
        dropoff_field = fields.return_time

        _log(log_dir, "time_dropoff_field_opening",
             selector=dropoff_field.selector, type=dropoff_field.element_kind)
        open_result_dropoff = await open_widget_reliably(
            session, dropoff_field, filler_logger, label="time_dropoff"
        )
        _snap(log_dir, "time_dropoff_after_click.html", open_result_dropoff.html_after)
        _log(log_dir, "time_dropoff_field_opened",
             opened=open_result_dropoff.opened, method=open_result_dropoff.method,
             options=open_result_dropoff.options_detected)

        if not open_result_dropoff.opened:
            return _fail(f"Could not open dropoff_time widget: {open_result_dropoff.error}",
                         pickup_result=pickup_result)

        cleaned_after_dropoff = clean_html_for_llm(open_result_dropoff.html_after)

        field_html_before_dropoff = _extract_element_html(
            cleaned_form, dropoff_field.selector, dropoff_field.selector_type
        )
        try:
            dropoff_widget, wc_cost2 = await classify_widget(
                field_html_before_dropoff, cleaned_after_dropoff,
                log_dir, client, label="time_dropoff_widget",
            )
        except Exception as exc:
            return _fail(f"dropoff_time widget classification failed: {exc}",
                         pickup_result=pickup_result)

        total_cost += wc_cost2
        _log(log_dir, "time_dropoff_widget_classified",
             widget_type=dropoff_widget.widget_type,
             is_searchable=dropoff_widget.is_searchable)

        try:
            dropoff_filler = get_filler_for_widget(dropoff_widget, match_mode="exact")
        except UnsupportedWidgetError as exc:
            return _fail(str(exc), pickup_result=pickup_result)

        dropoff_result = await dropoff_filler.fill(
            session, dropoff_field, dropoff_widget, dropoff_time, form_selector, filler_logger
        )
        _log(log_dir, "time_dropoff_filled",
             success=dropoff_result.success, matched=dropoff_result.matched_option)

        # ── 6. Verify final state ─────────────────────────────────────────────
        dropoff_verified = await is_field_at_target_time(session, dropoff_field, dropoff_time)
        _log(log_dir, "time_dropoff_verified", ok=dropoff_verified, target=dropoff_time)

        success = (
            pickup_result.success
            and dropoff_result.success
            and pickup_verified
            and dropoff_verified
        )

        if success:
            error = None
        elif not pickup_result.success:
            error = pickup_result.error
        elif not dropoff_result.success:
            error = dropoff_result.error
        elif not pickup_verified:
            error = f"Pickup time not in final form state (expected {pickup_time!r})"
        else:
            error = f"Dropoff time not in final form state (expected {dropoff_time!r})"

        _log(log_dir, "result",
             success=success,
             pickup_verified=pickup_verified,
             dropoff_verified=dropoff_verified,
             cost_eur=total_cost)

        return TimeFillExperimentReport(
            site_url=url,
            pickup_time_target=pickup_time,
            dropoff_time_target=dropoff_time,
            success=success,
            pickup_result=pickup_result,
            dropoff_result=dropoff_result,
            duration_seconds=time.monotonic() - t0,
            cost_estimate_eur=total_cost,
            error=error,
        )
