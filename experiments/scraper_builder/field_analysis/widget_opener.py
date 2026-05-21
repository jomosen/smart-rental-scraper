"""Generic widget opener: clicks a field and verifies the dropdown actually opened."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from browser_session import BrowserSession
from field_analysis.field_identifier import IdentifiedField
from session_logger import SessionLogger

_POLL_INTERVAL_MS = 200
_POLL_TIMEOUT_MS = 3_000

# Single JS call that returns all three open-state signals at once.
_JS_CHECK_STATE = """
() => {
    const comboboxes = document.querySelectorAll('[role="combobox"]');
    let expanded = false;
    for (const cb of comboboxes) {
        if (cb.getAttribute('aria-expanded') === 'true') { expanded = true; break; }
    }
    const listboxes = document.querySelectorAll('[role="listbox"]');
    let listboxVisible = false;
    for (const lb of listboxes) {
        if (lb.offsetParent !== null) { listboxVisible = true; break; }
    }
    const options = document.querySelectorAll('[role="option"]');
    let optionCount = 0;
    for (const el of options) {
        if (el.offsetParent !== null) optionCount++;
    }
    return { expanded, listboxVisible, optionCount };
}
"""


@dataclass
class WidgetOpenResult:
    opened: bool
    method: str          # "already_open" | "click_field" | "click_inner_combobox" | "failed"
    aria_expanded_after: bool | None
    options_detected: int
    html_after: str
    error: str | None


async def _check_state(session: BrowserSession) -> dict:
    return await session.page.evaluate(_JS_CHECK_STATE)


def _is_open(state: dict, baseline_options: int) -> bool:
    return (
        state["expanded"]
        or state["listboxVisible"]
        or state["optionCount"] > baseline_options
    )


async def _poll_until_open(session: BrowserSession, baseline_options: int) -> dict:
    """Poll up to _POLL_TIMEOUT_MS for the widget to appear open."""
    elapsed = 0
    while elapsed < _POLL_TIMEOUT_MS:
        state = await _check_state(session)
        if _is_open(state, baseline_options):
            return state
        await asyncio.sleep(_POLL_INTERVAL_MS / 1000.0)
        elapsed += _POLL_INTERVAL_MS
    return await _check_state(session)


async def open_widget_reliably(
    session: BrowserSession,
    field: IdentifiedField,
    logger: SessionLogger,
    label: str = "widget",
) -> WidgetOpenResult:
    """
    Open a dropdown/combobox and verify it is open before returning.

    Detection is ARIA-based and works for any widget that uses standard roles:
    considers the widget open when any of these is true:
      - A [role=combobox] has aria-expanded="true".
      - A [role=listbox] is visible (offsetParent !== null).
      - The count of visible [role=option] elements exceeds the baseline.

    Strategy:
    1. If already open, return immediately.
    2. Click field.selector; poll up to 3 s.
    3. If still closed, click the inner [role=combobox] or input (react-select
       pattern: the outer wrapper may not propagate the click to the inner input);
       poll again.
    4. If still closed, return opened=False — caller must NOT classify blindly.
    """
    def _log(**kw):
        logger.log("widget_opener", label=label, **kw)

    # ── Check initial state ───────────────────────────────────────────────
    init_state = await _check_state(session)
    baseline_options = init_state["optionCount"]

    if init_state["expanded"]:
        html_after = await session.get_html()
        _log(event="already_open", options=baseline_options)
        return WidgetOpenResult(
            opened=True, method="already_open",
            aria_expanded_after=True,
            options_detected=baseline_options,
            html_after=html_after, error=None,
        )

    # ── Attempt 1: click field.selector ──────────────────────────────────
    _log(event="attempt_click_field", selector=field.selector)
    await session.click_selector(field.selector, field.selector_type)
    state1 = await _poll_until_open(session, baseline_options)

    if _is_open(state1, baseline_options):
        html_after = await session.get_html()
        _log(event="opened_click_field",
             aria_expanded=state1["expanded"], options=state1["optionCount"])
        return WidgetOpenResult(
            opened=True, method="click_field",
            aria_expanded_after=state1["expanded"],
            options_detected=state1["optionCount"],
            html_after=html_after, error=None,
        )

    # ── Attempt 2: click inner [role=combobox] or input ───────────────────
    _log(event="attempt_click_inner_combobox", selector=field.selector)
    if field.selector_type == "css":
        inner_sel = f"{field.selector} [role='combobox'], {field.selector} input"
        try:
            inner_loc = session.page.locator(inner_sel).first
            await inner_loc.click(timeout=2_000)
        except Exception as exc:
            _log(event="inner_click_error", error=str(exc))
    else:
        # XPath: inner-element lookups would require complex XPath axes;
        # skip and let the poll report the current state.
        _log(event="inner_click_skipped",
             reason="XPath selector — inner element lookup not supported")

    state2 = await _poll_until_open(session, baseline_options)

    if _is_open(state2, baseline_options):
        html_after = await session.get_html()
        _log(event="opened_inner_combobox",
             aria_expanded=state2["expanded"], options=state2["optionCount"])
        return WidgetOpenResult(
            opened=True, method="click_inner_combobox",
            aria_expanded_after=state2["expanded"],
            options_detected=state2["optionCount"],
            html_after=html_after, error=None,
        )

    # ── Both attempts failed ──────────────────────────────────────────────
    html_after = await session.get_html()
    error_msg = (
        f"Widget did not open after 2 attempts "
        f"(aria_expanded={state2['expanded']}, options={state2['optionCount']})"
    )
    _log(event="failed",
         aria_expanded=state2["expanded"], options=state2["optionCount"])
    return WidgetOpenResult(
        opened=False, method="failed",
        aria_expanded_after=state2["expanded"],
        options_detected=state2["optionCount"],
        html_after=html_after, error=error_msg,
    )
