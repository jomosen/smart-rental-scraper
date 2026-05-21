"""Generic widget opener: clicks a field and verifies the dropdown actually opened."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from browser_session import BrowserSession
from field_analysis.field_identifier import IdentifiedField
from session_logger import SessionLogger

_POLL_INTERVAL_MS = 200
_POLL_TIMEOUT_MS = 3_000        # full timeout for primary attempts
_POLL_TIMEOUT_FAST_MS = 1_200   # shorter timeout for fallback strategies

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
    method: str   # see _METHODS below for the full list of possible values
    aria_expanded_after: bool | None
    options_detected: int
    html_after: str
    error: str | None

# method values:
# "already_open"         widget was already open before any interaction
# "click_field"          attempt 1: click on the identified field selector
# "click_inner_combobox" attempt 2: click on inner [role=combobox]/input
# "click_ancestor_N"     attempt 3: click the Nth ancestor of the field element
# "keyboard_ArrowDown"   attempt 4: focus + ArrowDown
# "keyboard_Enter"       attempt 4: focus + Enter
# "keyboard_Space"       attempt 4: focus + Space
# "failed"               all attempts failed


async def _check_state(session: BrowserSession) -> dict:
    return await session.page.evaluate(_JS_CHECK_STATE)


def _is_open(state: dict, baseline_options: int) -> bool:
    return (
        state["expanded"]
        or state["listboxVisible"]
        or state["optionCount"] > baseline_options
    )


async def _poll_until_open(
    session: BrowserSession,
    baseline_options: int,
    timeout_ms: int = _POLL_TIMEOUT_MS,
) -> dict:
    """Poll up to *timeout_ms* ms for the widget to appear open."""
    elapsed = 0
    while elapsed < timeout_ms:
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

    Detection is ARIA-based (no provider-specific assumptions):
    the widget is considered open when any of these is true:
      - A [role=combobox] has aria-expanded="true".
      - A [role=listbox] is visible (offsetParent !== null).
      - The count of visible [role=option] elements exceeds the baseline.

    Cascade of four strategies, tried in order:
    1. Click the field selector directly.
    2. Click the inner [role=combobox] or input child (for wrappers where
       the outer element does not propagate clicks to the input).
    3. Click ancestor containers (1–3 levels up) — handles widgets like
       react-select where the click handler lives on a wrapper div, not on
       the inner input.
    4. Focus the element + press standard ARIA open keys (ArrowDown,
       Enter, Space) — for accessible widgets that respond to keyboard.

    Stops as soon as a strategy opens the widget. Returns opened=False only
    if every strategy fails.
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
            state2 = await _poll_until_open(session, baseline_options,
                                            timeout_ms=_POLL_TIMEOUT_FAST_MS)
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
        except Exception as exc:
            _log(event="inner_click_error", error=str(exc))
    else:
        _log(event="inner_click_skipped",
             reason="XPath selector — inner element lookup not supported")

    # ── Attempt 3: click ancestor containers (1–3 levels up) ─────────────
    # Handles widgets where the click handler is on a wrapper div, not the
    # identified input itself (e.g. react-select's "control" div).
    if field.selector_type == "css":
        for level in range(1, 4):
            _log(event="attempt_click_ancestor", level=level)
            try:
                clicked = await session.page.evaluate(
                    """([sel, lvl]) => {
                        const el = document.querySelector(sel);
                        if (!el) return false;
                        let parent = el;
                        for (let i = 0; i < lvl; i++) {
                            parent = parent.parentElement;
                            if (!parent) return false;
                        }
                        if (parent.tagName === 'HTML' || parent.tagName === 'BODY')
                            return false;
                        if (parent.offsetParent === null) return false;
                        parent.click();
                        return true;
                    }""",
                    [field.selector, level],
                )
            except Exception as exc:
                _log(event="ancestor_click_error", level=level, error=str(exc))
                clicked = False

            if clicked:
                state3 = await _poll_until_open(session, baseline_options,
                                                timeout_ms=_POLL_TIMEOUT_FAST_MS)
                if _is_open(state3, baseline_options):
                    html_after = await session.get_html()
                    _log(event="opened_control_container", level=level,
                         aria_expanded=state3["expanded"], options=state3["optionCount"])
                    return WidgetOpenResult(
                        opened=True, method=f"click_ancestor_{level}",
                        aria_expanded_after=state3["expanded"],
                        options_detected=state3["optionCount"],
                        html_after=html_after, error=None,
                    )

    # ── Attempt 4: focus + standard ARIA open keys ────────────────────────
    # Accessible comboboxes open on ArrowDown, Enter, or Space.
    _log(event="attempt_keyboard", selector=field.selector)
    try:
        sel = (f"xpath={field.selector}"
               if field.selector_type == "xpath"
               else field.selector)
        loc = session.page.locator(sel).first
        await loc.focus(timeout=2_000)

        for key in ("ArrowDown", "Enter", "Space"):
            await session.page.keyboard.press(key)
            state4 = await _poll_until_open(session, baseline_options,
                                            timeout_ms=_POLL_TIMEOUT_FAST_MS)
            if _is_open(state4, baseline_options):
                html_after = await session.get_html()
                _log(event="opened_keyboard", key=key,
                     aria_expanded=state4["expanded"], options=state4["optionCount"])
                return WidgetOpenResult(
                    opened=True, method=f"keyboard_{key}",
                    aria_expanded_after=state4["expanded"],
                    options_detected=state4["optionCount"],
                    html_after=html_after, error=None,
                )
    except Exception as exc:
        _log(event="keyboard_attempt_error", error=str(exc))

    # ── All strategies failed ─────────────────────────────────────────────
    final_state = await _check_state(session)
    html_after = await session.get_html()
    error_msg = (
        f"Widget did not open after all 4 strategies "
        f"(aria_expanded={final_state['expanded']}, "
        f"options={final_state['optionCount']})"
    )
    _log(event="failed",
         aria_expanded=final_state["expanded"], options=final_state["optionCount"])
    return WidgetOpenResult(
        opened=False, method="failed",
        aria_expanded_after=final_state["expanded"],
        options_detected=final_state["optionCount"],
        html_after=html_after, error=error_msg,
    )
