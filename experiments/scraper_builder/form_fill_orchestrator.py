"""End-to-end form fill orchestrator: location → dates → times in one browser session."""
from __future__ import annotations

import dataclasses
import json
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from anthropic import AsyncAnthropic
from bs4 import BeautifulSoup

from browser_session import BrowserSession
from cookie_closer import close_cookies_in_session
from date_analysis.date_widget_classifier import classify_date_widget
from field_analysis.field_identifier import FormFields, IdentifiedField, identify_form_fields
from field_analysis.widget_classifier import classify_widget
from field_analysis.widget_opener import open_widget_reliably
from filling.date_filler_factory import UnsupportedDateWidgetError, get_date_filler
from filling.filler_factory import UnsupportedWidgetError, get_filler_for_widget
from filling.form_state import (
    FormInputSnapshot,
    capture_form_state,
    compare_states,
    is_field_at_target_date,
    is_field_at_target_time,
    save_snapshot as save_state_snapshot,
)
from form_capture.form_capturer import capture_search_form
from form_capture.html_cleaner import clean_html_for_llm
from session_logger import SessionLogger


# ── Internal helpers ──────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


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


async def _dismiss_open_widgets(session: BrowserSession) -> None:
    """Press Escape to close any open dropdown or calendar. Generic, no provider assumptions."""
    try:
        await session.page.keyboard.press("Escape")
        await session.wait_ms(300)
    except Exception:
        pass


async def is_location_filled(
    session: BrowserSession,
    location_field: IdentifiedField,
    form_selector: str,
    before_snapshot: FormInputSnapshot,
) -> bool:
    """
    Return True if filling location produced a persistent change:
    at least one form input went from empty to non-empty.
    Provider-agnostic — reuses form_state.
    """
    current = await capture_form_state(session, form_selector)
    diff = compare_states(before_snapshot, current)
    for old, new in diff.changed_inputs.values():
        if not old.strip() and new.strip():
            return True
    for val in diff.added_inputs.values():
        if val and val.strip():
            return True
    return False


# ── Report dataclasses ────────────────────────────────────────────────────────

@dataclass
class FieldFillOutcome:
    field_name: str
    attempted: bool
    success: bool
    strategy: str | None
    target: str
    error: str | None


@dataclass
class FormFillReport:
    site_url: str
    targets: dict
    success: bool
    outcomes: list[FieldFillOutcome]
    failed_at: str | None
    duration_seconds: float
    cost_estimate_eur: float
    llm_calls: int
    error: str | None


# ── Main orchestrator ─────────────────────────────────────────────────────────

async def fill_form_in_session(
    session: BrowserSession,
    url: str,
    targets: dict,
    log_dir: Path,
    client: AsyncAnthropic,
) -> tuple[FormFillReport, FormFields | None]:
    """
    Inner orchestrator — caller owns the BrowserSession.

    Navigates to *url*, closes cookies, identifies all fields (one LLM call),
    then fills them in order: location → dates → times.
    Returns (FormFillReport, FormFields) so the caller can access submit_button.
    FormFields is None when field identification fails before any fills.
    """
    t0 = time.monotonic()
    total_cost = 0.0
    llm_calls = 0
    outcomes: list[FieldFillOutcome] = []
    failed_at: str | None = None
    fields: FormFields | None = None
    widget_data: dict = {}

    def _fail_field(name: str, target: str, strategy: str | None, error: str) -> FieldFillOutcome:
        nonlocal failed_at
        if failed_at is None:
            failed_at = name
            _log(log_dir, "fail_fast_triggered", failed_at=name, error=error)
        return FieldFillOutcome(
            field_name=name, attempted=True, success=False,
            strategy=strategy, target=target, error=error,
        )

    def _skipped(name: str, target: str) -> FieldFillOutcome:
        return FieldFillOutcome(
            field_name=name, attempted=False, success=False,
            strategy=None, target=target, error="skipped due to earlier failure",
        )

    def _ok(name: str, target: str, strategy: str) -> FieldFillOutcome:
        return FieldFillOutcome(
            field_name=name, attempted=True, success=True,
            strategy=strategy, target=target, error=None,
        )

    _log(log_dir, "orchestrator_started", url=url, targets={
        k: v.isoformat() if hasattr(v, "isoformat") else v
        for k, v in targets.items()
    })

    loc_target = targets["location"]
    pickup_date_target: date = targets["pickup_date"]
    return_date_target: date = targets["return_date"]
    pickup_time_target: str = targets["pickup_time"]
    return_time_target: str = targets["return_time"]

    filler_logger = SessionLogger(log_dir)

    # ── 1. Navigate ───────────────────────────────────────────────────────────
    _log(log_dir, "navigate", url=url)
    nav_t0 = time.monotonic()
    await session.navigate(url)
    _log(log_dir, "navigate_complete",
         duration_ms=int((time.monotonic() - nav_t0) * 1000))

    # ── 2. Close cookies ──────────────────────────────────────────────────────
    cookie_result = await close_cookies_in_session(session, log_dir)
    total_cost += cookie_result.cost_estimate_eur
    llm_calls += 1
    _log(log_dir, "cookie_closer_invoked",
         success=cookie_result.success, cost_eur=cookie_result.cost_estimate_eur)

    # ── 3. Capture form + identify ALL fields (one LLM call) ──────────────────
    form_result = await capture_search_form(session, log_dir)
    if form_result is None:
        error = "Form not found in page HTML"
        _log(log_dir, "orchestrator_result", success=False, error=error)
        return FormFillReport(
            site_url=url, targets=targets, success=False,
            outcomes=[], failed_at="form_capture",
            duration_seconds=time.monotonic() - t0,
            cost_estimate_eur=total_cost, llm_calls=llm_calls, error=error,
        ), None

    raw_form, cleaned_form = form_result
    _log(log_dir, "form_captured",
         raw_bytes=len(raw_form.encode("utf-8")),
         cleaned_bytes=len(cleaned_form.encode("utf-8")))

    try:
        fields, field_cost = await identify_form_fields(cleaned_form, log_dir, client)
    except Exception as exc:
        error = f"field_identification failed: {exc}"
        _log(log_dir, "orchestrator_result", success=False, error=error)
        return FormFillReport(
            site_url=url, targets=targets, success=False,
            outcomes=[], failed_at="field_identification",
            duration_seconds=time.monotonic() - t0,
            cost_estimate_eur=total_cost, llm_calls=llm_calls, error=error,
        ), None

    total_cost += field_cost
    llm_calls += 1
    _log(log_dir, "fields_identified",
         pickup_location=fields.pickup_location.selector if fields.pickup_location else None,
         pickup_date=fields.pickup_date.selector if fields.pickup_date else None,
         return_date=fields.return_date.selector if fields.return_date else None,
         pickup_time=fields.pickup_time.selector if fields.pickup_time else None,
         return_time=fields.return_time.selector if fields.return_time else None)

    form_selector = await _detect_form_selector(session)
    _log(log_dir, "form_selector_detected", selector=form_selector)

    # ── 4. LOCATION ───────────────────────────────────────────────────────────
    loc_verified = False

    if fields.pickup_location is None:
        outcomes.append(_fail_field("pickup_location", loc_target, None,
                                    "pickup_location field not identified"))
    else:
        loc_field = fields.pickup_location
        _log(log_dir, "field_fill_started", name="pickup_location",
             selector=loc_field.selector)

        state_before_loc = await capture_form_state(session, form_selector)
        save_state_snapshot(log_dir, "loc_state_before.json", state_before_loc)

        open_loc = await open_widget_reliably(
            session, loc_field, filler_logger, label="location"
        )
        _snap(log_dir, "loc_after_open.html", open_loc.html_after)

        if not open_loc.opened:
            outcomes.append(_fail_field("pickup_location", loc_target, None,
                                        f"Could not open location widget: {open_loc.error}"))
        else:
            cleaned_after_loc = clean_html_for_llm(open_loc.html_after)
            field_html_loc = _extract_element_html(
                cleaned_form, loc_field.selector, loc_field.selector_type
            )
            try:
                loc_widget, wc = await classify_widget(
                    field_html_loc, cleaned_after_loc,
                    log_dir, client, label="loc_widget",
                )
                total_cost += wc
                llm_calls += 1
                _log(log_dir, "field_widget_classified", name="pickup_location",
                     widget_type=loc_widget.widget_type, is_searchable=loc_widget.is_searchable)
                widget_data["pickup_location"] = {
                    "kind": "widget",
                    **dataclasses.asdict(loc_widget),
                    "match_mode": "fuzzy",
                    "strategy": None,
                }
            except Exception as exc:
                loc_widget = None
                outcomes.append(_fail_field("pickup_location", loc_target, None,
                                            f"location widget classification failed: {exc}"))

            if failed_at is None:
                try:
                    loc_filler = get_filler_for_widget(loc_widget)
                except UnsupportedWidgetError as exc:
                    outcomes.append(_fail_field("pickup_location", loc_target, None, str(exc)))
                    loc_filler = None

            if failed_at is None:
                loc_result = await loc_filler.fill(
                    session, loc_field, loc_widget, loc_target, form_selector, filler_logger
                )
                _log(log_dir, "field_filled", name="pickup_location",
                     success=loc_result.success, strategy=loc_result.strategy_used)

                if not loc_result.success:
                    outcomes.append(_fail_field("pickup_location", loc_target,
                                                loc_result.strategy_used, loc_result.error))
                else:
                    outcomes.append(_ok("pickup_location", loc_target, loc_result.strategy_used))
                    loc_verified = await is_location_filled(
                        session, loc_field, form_selector, state_before_loc
                    )

        await _dismiss_open_widgets(session)
        _log(log_dir, "field_closed", name="pickup_location")
        save_state_snapshot(log_dir, "loc_state_after.json",
                            await capture_form_state(session, form_selector))

    # ── 5. PICKUP DATE ────────────────────────────────────────────────────────
    pickup_date_widget = None

    if failed_at is not None:
        outcomes.append(_skipped("pickup_date", str(pickup_date_target)))
    elif fields.pickup_date is None:
        outcomes.append(_fail_field("pickup_date", str(pickup_date_target), None,
                                    "pickup_date field not identified"))
    else:
        pickup_date_field = fields.pickup_date
        _log(log_dir, "field_fill_started", name="pickup_date",
             selector=pickup_date_field.selector)

        await session.click_selector(
            pickup_date_field.selector, pickup_date_field.selector_type
        )
        await session.wait_ms(800)
        html_after_pd = await session.get_html()
        _snap(log_dir, "pickup_date_after_click.html", html_after_pd)
        cleaned_after_pd = clean_html_for_llm(html_after_pd)

        field_html_pd = _extract_element_html(
            cleaned_form, pickup_date_field.selector, pickup_date_field.selector_type
        )
        try:
            pickup_date_widget, dw_cost = await classify_date_widget(
                field_html_pd, cleaned_after_pd,
                label="date_pickup", log_dir=log_dir, client=client,
            )
            total_cost += dw_cost
            llm_calls += 1
            _log(log_dir, "field_widget_classified", name="pickup_date",
                 widget_type=pickup_date_widget.widget_type,
                 is_range_calendar=pickup_date_widget.is_range_calendar)
            widget_data["pickup_date"] = {
                "kind": "date_widget",
                **dataclasses.asdict(pickup_date_widget),
                "strategy": None,
            }
        except Exception as exc:
            outcomes.append(_fail_field("pickup_date", str(pickup_date_target), None,
                                        f"pickup_date widget classification failed: {exc}"))

        if failed_at is None:
            try:
                pickup_date_filler = get_date_filler(pickup_date_widget)
            except UnsupportedDateWidgetError as exc:
                outcomes.append(_fail_field("pickup_date", str(pickup_date_target), None, str(exc)))
                pickup_date_filler = None

        if failed_at is None:
            pickup_date_result = await pickup_date_filler.fill(
                session, pickup_date_field, pickup_date_widget, pickup_date_target, filler_logger
            )
            _log(log_dir, "field_filled", name="pickup_date",
                 success=pickup_date_result.success, strategy=pickup_date_result.strategy_used)

            if not pickup_date_result.success:
                outcomes.append(_fail_field("pickup_date", str(pickup_date_target),
                                            pickup_date_result.strategy_used,
                                            pickup_date_result.error))
            else:
                outcomes.append(_ok("pickup_date", str(pickup_date_target),
                                    pickup_date_result.strategy_used))

    # ── 6. RETURN DATE ────────────────────────────────────────────────────────
    return_date_widget = None

    if failed_at is not None:
        outcomes.append(_skipped("return_date", str(return_date_target)))
    else:
        return_date_field = fields.return_date

        return_already_set = False
        if return_date_field is not None and pickup_date_widget is not None:
            return_already_set = await is_field_at_target_date(
                session, return_date_field, return_date_target,
                pickup_date_widget.date_format,
            )
            _log(log_dir, "return_date_pre_check",
                 already_set=return_already_set, target=str(return_date_target))

        if return_already_set:
            _log(log_dir, "return_date_autofilled_by_range_calendar")
            return_date_widget = pickup_date_widget
            outcomes.append(_ok("return_date", str(return_date_target),
                                "range_calendar_autofill"))
            widget_data["return_date"] = {
                "kind": "date_widget",
                **dataclasses.asdict(pickup_date_widget),
                "strategy": "range_calendar_autofill",
            }

        elif return_date_field is None:
            outcomes.append(_fail_field("return_date", str(return_date_target), None,
                                        "return_date field not identified and not auto-set"))
        else:
            _log(log_dir, "field_fill_started", name="return_date",
                 selector=return_date_field.selector)

            await session.click_selector(
                return_date_field.selector, return_date_field.selector_type
            )
            await session.wait_ms(800)
            html_after_rd = await session.get_html()
            _snap(log_dir, "return_date_after_click.html", html_after_rd)
            cleaned_after_rd = clean_html_for_llm(html_after_rd)

            field_html_rd = _extract_element_html(
                cleaned_form, return_date_field.selector, return_date_field.selector_type
            )
            try:
                return_date_widget, dw_cost2 = await classify_date_widget(
                    field_html_rd, cleaned_after_rd,
                    label="date_return", log_dir=log_dir, client=client,
                )
                total_cost += dw_cost2
                llm_calls += 1
                _log(log_dir, "field_widget_classified", name="return_date",
                     widget_type=return_date_widget.widget_type)
                widget_data["return_date"] = {
                    "kind": "date_widget",
                    **dataclasses.asdict(return_date_widget),
                    "strategy": None,
                }
            except Exception as exc:
                outcomes.append(_fail_field("return_date", str(return_date_target), None,
                                            f"return_date widget classification failed: {exc}"))

            if failed_at is None:
                try:
                    return_date_filler = get_date_filler(return_date_widget)
                except UnsupportedDateWidgetError as exc:
                    outcomes.append(_fail_field("return_date", str(return_date_target),
                                                None, str(exc)))
                    return_date_filler = None

            if failed_at is None:
                return_date_result = await return_date_filler.fill(
                    session, return_date_field, return_date_widget,
                    return_date_target, filler_logger
                )
                _log(log_dir, "field_filled", name="return_date",
                     success=return_date_result.success, strategy=return_date_result.strategy_used)

                if not return_date_result.success:
                    outcomes.append(_fail_field("return_date", str(return_date_target),
                                                return_date_result.strategy_used,
                                                return_date_result.error))
                else:
                    outcomes.append(_ok("return_date", str(return_date_target),
                                        return_date_result.strategy_used))

        await _dismiss_open_widgets(session)
        _log(log_dir, "field_closed", name="date_fields")
        save_state_snapshot(log_dir, "dates_state_after.json",
                            await capture_form_state(session, form_selector))

    # ── 7. PICKUP TIME ────────────────────────────────────────────────────────
    if failed_at is not None:
        outcomes.append(_skipped("pickup_time", pickup_time_target))
    elif fields.pickup_time is None:
        outcomes.append(_fail_field("pickup_time", pickup_time_target, None,
                                    "pickup_time field not identified"))
    else:
        pickup_time_field = fields.pickup_time
        _log(log_dir, "field_fill_started", name="pickup_time",
             selector=pickup_time_field.selector)

        open_pt = await open_widget_reliably(
            session, pickup_time_field, filler_logger, label="time_pickup"
        )
        _snap(log_dir, "pickup_time_after_open.html", open_pt.html_after)

        if not open_pt.opened:
            outcomes.append(_fail_field("pickup_time", pickup_time_target, None,
                                        f"Could not open pickup_time widget: {open_pt.error}"))
        else:
            cleaned_after_pt = clean_html_for_llm(open_pt.html_after)
            field_html_pt = _extract_element_html(
                cleaned_form, pickup_time_field.selector, pickup_time_field.selector_type
            )
            try:
                pt_widget, wc = await classify_widget(
                    field_html_pt, cleaned_after_pt,
                    log_dir, client, label="time_pickup_widget",
                )
                total_cost += wc
                llm_calls += 1
                _log(log_dir, "field_widget_classified", name="pickup_time",
                     widget_type=pt_widget.widget_type, is_searchable=pt_widget.is_searchable)
                widget_data["pickup_time"] = {
                    "kind": "widget",
                    **dataclasses.asdict(pt_widget),
                    "match_mode": "exact",
                    "strategy": None,
                }
            except Exception as exc:
                pt_widget = None
                outcomes.append(_fail_field("pickup_time", pickup_time_target, None,
                                            f"pickup_time widget classification failed: {exc}"))

            if failed_at is None:
                try:
                    pt_filler = get_filler_for_widget(pt_widget, match_mode="exact")
                except UnsupportedWidgetError as exc:
                    outcomes.append(_fail_field("pickup_time", pickup_time_target, None, str(exc)))
                    pt_filler = None

            if failed_at is None:
                pt_result = await pt_filler.fill(
                    session, pickup_time_field, pt_widget,
                    pickup_time_target, form_selector, filler_logger
                )
                _log(log_dir, "field_filled", name="pickup_time",
                     success=pt_result.success, strategy=pt_result.strategy_used)

                if not pt_result.success:
                    outcomes.append(_fail_field("pickup_time", pickup_time_target,
                                                pt_result.strategy_used, pt_result.error))
                else:
                    outcomes.append(_ok("pickup_time", pickup_time_target, pt_result.strategy_used))

        await _dismiss_open_widgets(session)
        _log(log_dir, "field_closed", name="pickup_time")

    # ── 8. RETURN TIME ────────────────────────────────────────────────────────
    if failed_at is not None:
        outcomes.append(_skipped("return_time", return_time_target))
    elif fields.return_time is None:
        outcomes.append(_fail_field("return_time", return_time_target, None,
                                    "return_time field not identified"))
    else:
        return_time_field = fields.return_time
        _log(log_dir, "field_fill_started", name="return_time",
             selector=return_time_field.selector)

        open_rt = await open_widget_reliably(
            session, return_time_field, filler_logger, label="time_return"
        )
        _snap(log_dir, "return_time_after_open.html", open_rt.html_after)

        if not open_rt.opened:
            outcomes.append(_fail_field("return_time", return_time_target, None,
                                        f"Could not open return_time widget: {open_rt.error}"))
        else:
            cleaned_after_rt = clean_html_for_llm(open_rt.html_after)
            field_html_rt = _extract_element_html(
                cleaned_form, return_time_field.selector, return_time_field.selector_type
            )
            try:
                rt_widget, wc = await classify_widget(
                    field_html_rt, cleaned_after_rt,
                    log_dir, client, label="time_return_widget",
                )
                total_cost += wc
                llm_calls += 1
                _log(log_dir, "field_widget_classified", name="return_time",
                     widget_type=rt_widget.widget_type, is_searchable=rt_widget.is_searchable)
                widget_data["return_time"] = {
                    "kind": "widget",
                    **dataclasses.asdict(rt_widget),
                    "match_mode": "exact",
                    "strategy": None,
                }
            except Exception as exc:
                rt_widget = None
                outcomes.append(_fail_field("return_time", return_time_target, None,
                                            f"return_time widget classification failed: {exc}"))

            if failed_at is None:
                try:
                    rt_filler = get_filler_for_widget(rt_widget, match_mode="exact")
                except UnsupportedWidgetError as exc:
                    outcomes.append(_fail_field("return_time", return_time_target, None, str(exc)))
                    rt_filler = None

            if failed_at is None:
                rt_result = await rt_filler.fill(
                    session, return_time_field, rt_widget,
                    return_time_target, form_selector, filler_logger
                )
                _log(log_dir, "field_filled", name="return_time",
                     success=rt_result.success, strategy=rt_result.strategy_used)

                if not rt_result.success:
                    outcomes.append(_fail_field("return_time", return_time_target,
                                                rt_result.strategy_used, rt_result.error))
                else:
                    outcomes.append(_ok("return_time", return_time_target, rt_result.strategy_used))

        await _dismiss_open_widgets(session)
        _log(log_dir, "field_closed", name="return_time")

    # ── 9. FINAL VERIFICATION ─────────────────────────────────────────────────
    def _outcome_succeeded(name: str) -> bool:
        return any(o.field_name == name and o.success for o in outcomes)

    pd_verified = False
    if fields.pickup_date is not None and pickup_date_widget is not None and _outcome_succeeded("pickup_date"):
        pd_verified = await is_field_at_target_date(
            session, fields.pickup_date, pickup_date_target, pickup_date_widget.date_format,
        )

    effective_rd_field = fields.return_date
    rd_verified = False
    eff_rd_widget = return_date_widget
    if effective_rd_field is not None and eff_rd_widget is not None and _outcome_succeeded("return_date"):
        rd_verified = await is_field_at_target_date(
            session, effective_rd_field, return_date_target, eff_rd_widget.date_format,
        )

    pt_verified = False
    if fields.pickup_time is not None and _outcome_succeeded("pickup_time"):
        pt_verified = await is_field_at_target_time(
            session, fields.pickup_time, pickup_time_target,
        )

    rt_verified = False
    if fields.return_time is not None and _outcome_succeeded("return_time"):
        rt_verified = await is_field_at_target_time(
            session, fields.return_time, return_time_target,
        )

    verification = {
        "pickup_location": loc_verified,
        "pickup_date": pd_verified,
        "return_date": rd_verified,
        "pickup_time": pt_verified,
        "return_time": rt_verified,
    }
    _log(log_dir, "final_verification", **verification)

    save_state_snapshot(log_dir, "final_form_state.json",
                        await capture_form_state(session, form_selector))

    success = failed_at is None and all(verification.values())

    error: str | None = None
    if failed_at is not None:
        failed_outcome = next((o for o in outcomes if o.field_name == failed_at), None)
        error = failed_outcome.error if failed_outcome else f"Failed at {failed_at}"
    elif not success:
        unverified = [k for k, v in verification.items() if not v]
        error = f"Final verification failed for: {unverified}"

    _log(log_dir, "orchestrator_result",
         success=success, failed_at=failed_at,
         verification=verification, cost_eur=total_cost, llm_calls=llm_calls)

    if widget_data:
        (log_dir / "form_widget_infos.json").write_text(
            json.dumps(widget_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return FormFillReport(
        site_url=url,
        targets=targets,
        success=success,
        outcomes=outcomes,
        failed_at=failed_at,
        duration_seconds=time.monotonic() - t0,
        cost_estimate_eur=total_cost,
        llm_calls=llm_calls,
        error=error,
    ), fields


async def fill_form(
    url: str,
    targets: dict,
    log_dir: Path,
    headless: bool = True,
) -> FormFillReport:
    """Fills the entire search form; creates its own browser session."""
    client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    async with BrowserSession(headless=headless) as session:
        report, _ = await fill_form_in_session(session, url, targets, log_dir, client)
        return report
