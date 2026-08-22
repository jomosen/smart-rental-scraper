"""Generic widget opener: clicks a field and verifies the dropdown actually opened."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ..browser_session import BrowserSession
from .field_identifier import IdentifiedField
from ..session_logger import SessionLogger

_POLL_INTERVAL_MS = 200
_POLL_TIMEOUT_MS = 3_000        # full timeout for primary attempts
_POLL_TIMEOUT_FAST_MS = 1_200   # shorter timeout for fallback strategies

# Single JS call that returns all three open-state signals at once.
# The `expanded` signal is scoped to the field being opened (the combobox is
# the field itself, inside it, or wrapping it): sites can leave a STALE
# aria-expanded="true" on an unrelated widget (e.g. a time dropdown that never
# resets after selection), and a page-global check then reports every later
# widget as instantly open. Listbox/option visibility stays global — those
# reflect something actually rendered right now.
_JS_CHECK_STATE = """
(fieldSel) => {
    let field = null;
    if (fieldSel) {
        try { field = document.querySelector(fieldSel); } catch (e) { field = null; }
    }
    const near = (el) =>
        !field || el === field || el.contains(field) || field.contains(el);
    const comboboxes = document.querySelectorAll('[role="combobox"]');
    let expanded = false;
    for (const cb of comboboxes) {
        if (cb.getAttribute('aria-expanded') === 'true' && near(cb)) {
            expanded = true; break;
        }
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
# "type_probe"           attempt 4: type a prefix of the target value — for
#                        type-ahead autocompletes that only render suggestions
#                        after input (requires probe_text)
# "keyboard_ArrowDown"   attempt 5: focus + ArrowDown
# "keyboard_Enter"       attempt 5: focus + Enter
# "keyboard_Space"       attempt 5: focus + Space
# "failed"               all attempts failed

# Count visible, smallish elements whose text contains the probe. Used as the
# open signal for suggestion lists that expose NO ARIA roles at all (no
# combobox/listbox/option): if typing makes new elements mentioning the probe
# appear, the widget is open even though ARIA says nothing.
_JS_COUNT_PROBE_MATCHES = """
(probe) => {
    const needle = probe.toLowerCase();
    let count = 0;
    for (const el of document.querySelectorAll('body *')) {
        if (el.offsetParent === null) continue;
        const tag = el.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SCRIPT' || tag === 'STYLE')
            continue;
        const txt = (el.textContent || '').trim();
        if (txt.length === 0 || txt.length > 300) continue;
        if (txt.toLowerCase().includes(needle)) count++;
    }
    return count;
}
"""


async def _check_state(
    session: BrowserSession, field_sel: str | None = None
) -> dict:
    return await session.page.evaluate(_JS_CHECK_STATE, field_sel)


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
    field_sel: str | None = None,
) -> dict:
    """Poll up to *timeout_ms* ms for the widget to appear open."""
    elapsed = 0
    while elapsed < timeout_ms:
        state = await _check_state(session, field_sel)
        if _is_open(state, baseline_options):
            return state
        await asyncio.sleep(_POLL_INTERVAL_MS / 1000.0)
        elapsed += _POLL_INTERVAL_MS
    return await _check_state(session, field_sel)


async def open_widget_reliably(
    session: BrowserSession,
    field: IdentifiedField,
    logger: SessionLogger,
    label: str = "widget",
    probe_text: str | None = None,
) -> WidgetOpenResult:
    """
    Open a dropdown/combobox and verify it is open before returning.

    Detection is ARIA-based (no provider-specific assumptions):
    the widget is considered open when any of these is true:
      - A [role=combobox] has aria-expanded="true".
      - A [role=listbox] is visible (offsetParent !== null).
      - The count of visible [role=option] elements exceeds the baseline.
    During the type_probe attempt only, a fourth signal applies: new visible
    elements containing the typed probe text (for suggestion lists that expose
    no ARIA roles at all).

    Cascade of strategies, tried in order:
    1. Click the field selector directly.
    2. Click the inner [role=combobox] or input child (for wrappers where
       the outer element does not propagate clicks to the input).
    3. Click ancestor containers (1–3 levels up) — handles widgets like
       react-select where the click handler lives on a wrapper div, not on
       the inner input.
    4. Type a short prefix of *probe_text* into the field — type-ahead
       autocompletes render suggestions only after real input. Skipped when
       probe_text is None. On success the typed prefix is left in the field
       (clearing it would close the dropdown the caller wants to inspect);
       fillers clear the field before typing the full value anyway.
    5. Focus the element + press standard ARIA open keys (ArrowDown,
       Enter, Space) — for accessible widgets that respond to keyboard.

    Stops as soon as a strategy opens the widget. Returns opened=False only
    if every strategy fails.
    """
    def _log(**kw):
        logger.log("widget_opener", label=label, **kw)

    # ── Check initial state ───────────────────────────────────────────────
    field_css = (field.selector if field.selector_type == "css" else None)
    init_state = await _check_state(session, field_css)
    baseline_options = init_state["optionCount"]

    # "Already open" needs visible options, not just an expanded flag: a stale
    # aria-expanded left behind by a previously-filled widget otherwise skips
    # the open click, and the later option click fails on an invisible list.
    if init_state["expanded"] and baseline_options > 0:
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
    state1 = await _poll_until_open(session, baseline_options, field_sel=field_css)

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
                                            timeout_ms=_POLL_TIMEOUT_FAST_MS,
                                            field_sel=field_css)
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
                                                timeout_ms=_POLL_TIMEOUT_FAST_MS,
                                                field_sel=field_css)
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

    # ── Attempt 4: type a prefix of the target value (type-ahead) ─────────
    # Autocompletes like a plain <input> with a suggestions div render options
    # only after real typed input — clicks and ARIA keys do nothing. The open
    # signal here is EITHER the ARIA state OR new visible elements containing
    # the typed probe (suggestion lists often carry no roles at all).
    if probe_text:
        probe = probe_text[:4] if len(probe_text) >= 4 else probe_text
        _log(event="attempt_type_probe", selector=field.selector, probe=probe)
        try:
            sel = (f"xpath={field.selector}"
                   if field.selector_type == "xpath"
                   else field.selector)
            loc = session.page.locator(sel).first
            await loc.click(timeout=2_000)
            await loc.fill("")
            baseline_probe = await session.page.evaluate(
                _JS_COUNT_PROBE_MATCHES, probe
            )
            await loc.press_sequentially(probe, delay=100)

            elapsed = 0
            state_t: dict | None = None
            probe_after = baseline_probe
            while elapsed < _POLL_TIMEOUT_MS:
                state_t = await _check_state(session, field_css)
                probe_after = await session.page.evaluate(
                    _JS_COUNT_PROBE_MATCHES, probe
                )
                if _is_open(state_t, baseline_options) or probe_after > baseline_probe:
                    break
                await asyncio.sleep(_POLL_INTERVAL_MS / 1000.0)
                elapsed += _POLL_INTERVAL_MS

            if state_t is not None and (
                _is_open(state_t, baseline_options) or probe_after > baseline_probe
            ):
                html_after = await session.get_html()
                _log(event="opened_type_probe",
                     aria_expanded=state_t["expanded"],
                     options=state_t["optionCount"],
                     probe_matches_before=baseline_probe,
                     probe_matches_after=probe_after)
                return WidgetOpenResult(
                    opened=True, method="type_probe",
                    aria_expanded_after=state_t["expanded"],
                    options_detected=max(state_t["optionCount"],
                                         probe_after - baseline_probe),
                    html_after=html_after, error=None,
                )

            # Not opened: leave the field clean for the keyboard attempt.
            await loc.fill("")
        except Exception as exc:
            _log(event="type_probe_error", error=str(exc))

    # ── Attempt 5: focus + standard ARIA open keys ────────────────────────
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
                                            timeout_ms=_POLL_TIMEOUT_FAST_MS,
                                            field_sel=field_css)
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
    final_state = await _check_state(session, field_css)
    html_after = await session.get_html()
    error_msg = (
        f"Widget did not open after all strategies "
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
