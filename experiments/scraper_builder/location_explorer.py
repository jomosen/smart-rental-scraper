"""Orchestrator: navigate → close cookies → find form → identify fields → classify location widget."""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from anthropic import AsyncAnthropic
from bs4 import BeautifulSoup

from browser_session import BrowserSession
from cookie_closer import close_cookies_in_session
from form_capture.form_capturer import capture_search_form
from form_capture.html_cleaner import clean_html_for_llm
from field_analysis.field_identifier import FormFields, identify_form_fields
from field_analysis.widget_classifier import WidgetInfo, classify_widget

_BASE_LOGS = Path(__file__).parent / "logs"


# ── Log helpers ──────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _log(log_dir: Path, event_type: str, **kwargs) -> None:
    event = {"ts": _now(), "type": event_type, **kwargs}
    with open(log_dir / "trace.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _snap(log_dir: Path, filename: str, content: str) -> None:
    (log_dir / "dom_snapshots" / filename).write_text(content, encoding="utf-8")


def _extract_element_html(html: str, selector: str, selector_type: str) -> str:
    """Return HTML of the element matched by selector, or full html as fallback."""
    if selector_type == "css":
        try:
            soup = BeautifulSoup(html, "html.parser")
            elements = soup.select(selector)
            if elements:
                return str(elements[0])
        except Exception:
            pass
    return html


# ── Public API ───────────────────────────────────────────────────────────────

def create_log_dir(site_name: str) -> Path:
    """Create and return logs/<ts>_<site_name>_location/ with subdirs."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    log_dir = _BASE_LOGS / f"{ts}_{site_name}_location"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "dom_snapshots").mkdir(exist_ok=True)
    (log_dir / "llm_calls").mkdir(exist_ok=True)
    return log_dir


@dataclass
class LocationExplorationReport:
    success: bool
    site_name: str
    site_url: str
    form_html_size: int | None
    form_html_cleaned_size: int | None
    all_fields: FormFields | None
    location_widget: WidgetInfo | None
    error: str | None
    duration_seconds: float
    cost_estimate_eur: float
    llm_calls: int
    log_dir: str


async def explore_location_field(
    site_name: str,
    url: str,
    log_dir: Path,
    headless: bool = True,
) -> LocationExplorationReport:
    """
    Full flow:
    1. Open browser + navigate to url.
    2. Close cookie banner (mandatory; continues even if it fails).
    3. Capture + clean search form HTML.
    4. LLM identifies all form fields.
    5. Click pickup_location field.
    6. Capture post-click HTML.
    7. LLM classifies the widget type.
    """
    t0 = time.monotonic()
    total_cost = 0.0
    n_llm_calls = 0
    client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    def _fail(msg: str) -> LocationExplorationReport:
        _log(log_dir, "result", success=False, error=msg)
        return LocationExplorationReport(
            success=False, site_name=site_name, site_url=url,
            form_html_size=None, form_html_cleaned_size=None,
            all_fields=None, location_widget=None,
            error=msg, duration_seconds=time.monotonic() - t0,
            cost_estimate_eur=total_cost, llm_calls=n_llm_calls,
            log_dir=str(log_dir),
        )

    async with BrowserSession(headless=headless) as session:
        # 1. Navigate
        _log(log_dir, "navigate", url=url)
        nav_t0 = time.monotonic()
        await session.navigate(url)
        _log(log_dir, "navigate_complete",
             url=url, duration_ms=int((time.monotonic() - nav_t0) * 1000))

        # 2. Close cookies — mandatory before form capture; continue even if it fails
        cookie_result = await close_cookies_in_session(session, log_dir)
        total_cost += cookie_result.cost_estimate_eur
        _log(log_dir, "cookie_closer_invoked",
             action=cookie_result.action, success=cookie_result.success,
             cost_eur=cookie_result.cost_estimate_eur)

        # 3. Capture form
        form_result = await capture_search_form(session, log_dir)
        if form_result is None:
            return _fail("Form not found in page HTML")

        raw_form, cleaned_form = form_result
        raw_size = len(raw_form.encode("utf-8"))
        clean_size = len(cleaned_form.encode("utf-8"))
        _log(log_dir, "form_located",
             raw_bytes=raw_size, cleaned_bytes=clean_size,
             reduction_pct=round(100 * (1 - clean_size / raw_size), 1))

        # 4. Identify fields
        try:
            fields, field_cost = await identify_form_fields(cleaned_form, log_dir, client)
        except Exception as exc:
            return _fail(f"field_identification failed: {exc}")

        total_cost += field_cost
        n_llm_calls += 1
        _log(log_dir, "llm_call_field_identification", cost_eur=field_cost)
        for fname in ("pickup_location", "pickup_date", "pickup_time",
                      "return_location", "return_date", "return_time", "submit_button"):
            field = getattr(fields, fname)
            if field:
                _log(log_dir, "field_identified", field=fname,
                     selector=field.selector, kind=field.element_kind)

        if fields.pickup_location is None:
            _log(log_dir, "result", success=False, error="pickup_location not identified")
            return LocationExplorationReport(
                success=False, site_name=site_name, site_url=url,
                form_html_size=raw_size, form_html_cleaned_size=clean_size,
                all_fields=fields, location_widget=None,
                error="pickup_location not identified",
                duration_seconds=time.monotonic() - t0,
                cost_estimate_eur=total_cost, llm_calls=n_llm_calls,
                log_dir=str(log_dir),
            )

        # 5. Click pickup_location
        pickup = fields.pickup_location
        _log(log_dir, "click_location_field",
             selector=pickup.selector, selector_type=pickup.selector_type)
        await session.click_selector(pickup.selector, pickup.selector_type)
        await asyncio.sleep(1.5)

        # 6. Capture post-click HTML
        html_after = await session.get_html()
        _snap(log_dir, "04_after_click.html", html_after)
        cleaned_after = clean_html_for_llm(html_after)
        _log(log_dir, "html_captured_after_click",
             size_bytes=len(html_after.encode("utf-8")))

        # 7. Classify widget
        field_html_before = _extract_element_html(
            cleaned_form, pickup.selector, pickup.selector_type
        )
        try:
            widget, widget_cost = await classify_widget(
                field_html_before, cleaned_after, log_dir, client
            )
        except Exception as exc:
            return LocationExplorationReport(
                success=False, site_name=site_name, site_url=url,
                form_html_size=raw_size, form_html_cleaned_size=clean_size,
                all_fields=fields, location_widget=None,
                error=f"widget_classification failed: {exc}",
                duration_seconds=time.monotonic() - t0,
                cost_estimate_eur=total_cost, llm_calls=n_llm_calls,
                log_dir=str(log_dir),
            )

        total_cost += widget_cost
        n_llm_calls += 1
        _log(log_dir, "llm_call_widget_classification", cost_eur=widget_cost)
        _log(log_dir, "widget_classified",
             widget_type=widget.widget_type, is_searchable=widget.is_searchable,
             options_container=widget.options_container_selector)

        report = LocationExplorationReport(
            success=True, site_name=site_name, site_url=url,
            form_html_size=raw_size, form_html_cleaned_size=clean_size,
            all_fields=fields, location_widget=widget,
            error=None,
            duration_seconds=time.monotonic() - t0,
            cost_estimate_eur=total_cost, llm_calls=n_llm_calls,
            log_dir=str(log_dir),
        )
        _log(log_dir, "result", success=True,
             widget_type=widget.widget_type, cost_eur=total_cost)
        return report
