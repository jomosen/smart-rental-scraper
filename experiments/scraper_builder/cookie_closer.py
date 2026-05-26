"""
Cookie-banner closer — two entry points:

- close_cookies_in_session(session, log_dir): assumes page already loaded,
  shares the caller's log directory.
- close_cookies(url, site_name, headless): standalone; creates its own
  browser session, navigates, then calls close_cookies_in_session.
"""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

from banner_detector import extract_banner_candidates
from browser_session import BrowserSession
from llm_selector import ask_llm
from models import CloseResult
from session_logger import SessionLogger

_MAX_ATTEMPTS = 3

# Adaptive banner polling — replaces the old single wait_for_selector.
_POLL_INTERVAL_S = 1.0           # seconds between poll iterations
_BANNER_POLL_TIMEOUT_S = 15.0    # total wait budget for async banner injection
_HTML_GROWTH_THRESHOLD = 5_000   # chars growth from baseline → async content injected
_SETTLE_S = 0.3                  # pause after signal so fade-in animations finish

# Multilingual accept-button text patterns used by the heuristic closer.
# Ordered most-specific first to reduce false positives.
_ACCEPT_TEXTS = [
    "accept all cookies", "accept all", "accept cookies",
    "aceptar todas", "aceptar todo", "aceptar cookies", "aceptar",
    "agree and continue", "agree", "got it", "i understand", "entendido",
    "allow all", "allow cookies", "continuar", "continue",
    "tout accepter", "alle akzeptieren", "accetta tutto",
    "consent", "ok",
]

# JS: check two generic banner signals in a single DOM walk.
#
# html_growth — document grew > _HTML_GROWTH_THRESHOLD chars from the baseline
#               captured right after domcontentloaded; async CMP injection detected.
#
# overlay     — a direct body child or dialog element is fixed/absolute,
#               z-index ≥ 100, and covers enough of the viewport
#               (height > 50 px AND width > 50 % of viewport width).
#               Catches both full-screen modals and sticky bottom banners.
#
# Only body's direct children + semantic dialog elements are inspected
# (not querySelectorAll('*')) to keep the check fast on large DOMs.
_POLL_SIGNALS_JS = """
([baselineLen]) => {
    const currentLen = document.documentElement.innerHTML.length;
    const growth = currentLen - baselineLen;
    const vw = window.innerWidth;

    const toCheck = [
        ...document.body.children,
        ...document.querySelectorAll('[role="dialog"],[role="alertdialog"]'),
    ];

    let hasOverlay = false;
    for (const el of toCheck) {
        const s = window.getComputedStyle(el);
        if (s.position !== 'fixed' && s.position !== 'absolute') continue;
        const z = parseInt(s.zIndex || '0', 10);
        if (isNaN(z) || z < 100) continue;
        const r = el.getBoundingClientRect();
        if (r.height > 50 && r.width > vw * 0.5) { hasOverlay = true; break; }
    }

    return {growth, hasOverlay};
}
"""


# JS: collect visible dialog/modal elements from the live DOM.
# Targets semantic patterns only — no provider-specific selectors.
_COLLECT_MODALS_JS = """
() => {
    const results = [];
    const seen = new Set();
    const selectors = [
        'dialog',
        '[aria-modal="true"]',
        '[role="dialog"]',
        '[role="alertdialog"]',
    ];
    for (const sel of selectors) {
        for (const el of document.querySelectorAll(sel)) {
            const r = el.getBoundingClientRect();
            if (r.width === 0 && r.height === 0) continue;
            const html = el.outerHTML.substring(0, 2000);
            if (seen.has(html)) continue;
            seen.add(html);
            results.push({html, tag: el.tagName.toLowerCase()});
        }
    }
    // Fallback: high z-index fixed/absolute overlays not caught above.
    if (results.length === 0) {
        const vw = window.innerWidth;
        for (const el of document.body.children) {
            const s = window.getComputedStyle(el);
            if (s.position !== 'fixed' && s.position !== 'absolute') continue;
            const z = parseInt(s.zIndex || '0', 10);
            if (isNaN(z) || z < 100) continue;
            const r = el.getBoundingClientRect();
            if (r.height > 50 && r.width > vw * 0.5) {
                const html = el.outerHTML.substring(0, 2000);
                if (!seen.has(html)) {
                    seen.add(html);
                    results.push({html, tag: el.tagName.toLowerCase()});
                }
            }
        }
    }
    return results;
}
"""

# JS: check whether any modal/overlay is still blocking the viewport.
_CHECK_OVERLAY_JS = """
() => {
    const vw = window.innerWidth;
    const candidates = [
        ...document.querySelectorAll('dialog'),
        ...document.querySelectorAll('[aria-modal="true"]'),
        ...document.querySelectorAll('[role="dialog"],[role="alertdialog"]'),
        ...document.body.children,
    ];
    for (const el of candidates) {
        const s = window.getComputedStyle(el);
        const isDialog = el.tagName.toLowerCase() === 'dialog';
        const isFixed = s.position === 'fixed' || s.position === 'absolute' || isDialog;
        if (!isFixed) continue;
        const z = parseInt(s.zIndex || '0', 10);
        if (!isDialog && (isNaN(z) || z < 100)) continue;
        const r = el.getBoundingClientRect();
        if (r.height > 50 && r.width > vw * 0.5) {
            return {present: true, html: el.outerHTML.substring(0, 2000), tag: el.tagName.toLowerCase()};
        }
    }
    return {present: false, html: null, tag: null};
}
"""


def _result_fields(r: CloseResult) -> dict:
    return {"success": r.success, "action": r.action, "selector": r.selector, "attempts": r.attempts}


async def _get_html_length(session: BrowserSession) -> int:
    """Return document.documentElement.innerHTML.length (cheap, no Python serialisation)."""
    try:
        return await session.page.evaluate(
            "() => document.documentElement.innerHTML.length"
        )
    except Exception:
        return 0


async def _collect_live_modal_candidates(session: BrowserSession) -> list[dict]:
    """
    Query the live DOM for dialog/aria-modal elements and high-z-index overlays.
    Returns candidates in the same format as extract_banner_candidates.
    """
    try:
        items = await session.page.evaluate(_COLLECT_MODALS_JS)
        return [{"html": item["html"], "tag": item["tag"], "score": 99} for item in items]
    except Exception:
        return []


async def _check_overlay_present(session: BrowserSession) -> tuple[bool, dict | None]:
    """
    Check whether a modal/overlay is still blocking a significant portion of the viewport.
    Returns (present, candidate_dict | None).
    """
    try:
        result = await session.page.evaluate(_CHECK_OVERLAY_JS)
        if result.get("present"):
            return True, {"html": result["html"], "tag": result["tag"], "score": 99}
        return False, None
    except Exception:
        return False, None


async def _poll_for_banner_signal(
    session: BrowserSession,
    baseline_len: int,
    logger: SessionLogger,
) -> tuple[bool, str]:
    """
    Poll for generic banner signals every _POLL_INTERVAL_S for up to
    _BANNER_POLL_TIMEOUT_S seconds.

    Signals (provider-agnostic — no CMP-specific selectors):
      html_growth — HTML grew > _HTML_GROWTH_THRESHOLD chars since baseline,
                    indicating an async script injected content (e.g. a CMP module).
      overlay     — a visible fixed/absolute element with z-index ≥ 100 and
                    height > 50 px + width > 50 % of viewport.

    Returns (signal_found, signal_type) where signal_type is
    "html_growth" | "overlay" | "timeout".
    """
    t0 = time.monotonic()
    poll_n = 0

    while True:
        elapsed_s = time.monotonic() - t0
        if elapsed_s >= _BANNER_POLL_TIMEOUT_S:
            logger.log(
                "banner_poll_timeout",
                polls=poll_n,
                elapsed_ms=int(elapsed_s * 1000),
            )
            return False, "timeout"

        try:
            result = await session.page.evaluate(_POLL_SIGNALS_JS, [baseline_len])
        except Exception:
            result = {"growth": 0, "hasOverlay": False}

        growth = result.get("growth", 0)
        has_overlay = result.get("hasOverlay", False)

        signal: str | None = None
        if growth >= _HTML_GROWTH_THRESHOLD:
            signal = "html_growth"
        elif has_overlay:
            signal = "overlay"

        logger.log(
            "banner_poll",
            poll=poll_n,
            elapsed_ms=int(elapsed_s * 1000),
            growth=growth,
            has_overlay=has_overlay,
            signal=signal,
        )

        if signal:
            return True, signal

        await asyncio.sleep(_POLL_INTERVAL_S)
        poll_n += 1


async def _close_banner_heuristic(
    session: BrowserSession,
    logger: SessionLogger,
) -> bool:
    """
    Click the first visible accept-like button without LLM.

    Scans the live DOM for buttons/links matching multilingual accept patterns.
    Returns True if a button was found and clicked.
    """
    for text in _ACCEPT_TEXTS:
        try:
            loc = (
                session.page
                .locator('button, a, [role="button"]')
                .filter(has_text=re.compile(text, re.IGNORECASE))
                .first
            )
            if await loc.is_visible():
                await loc.click(timeout=2_000)
                logger.log("heuristic_banner_click", matched_text=text)
                return True
        except Exception:
            continue
    logger.log("heuristic_banner_click_failed")
    return False


async def close_cookies_in_session(
    session: BrowserSession,
    log_dir: Path,
    heuristic_only: bool = False,
) -> CloseResult:
    """
    Close the cookie banner on the currently loaded page.

    Does not navigate — assumes the caller already loaded the page.
    Appends events to log_dir/trace.jsonl and saves snapshots/llm_calls
    into the same log_dir, making all logs inspectable in one place.
    """
    logger = SessionLogger(log_dir)
    t0 = time.monotonic()
    total_cost = 0.0

    # ── Adaptive banner poll ──────────────────────────────────────────────────
    # Capture the HTML size right after page load as the baseline.
    # Then poll cheap generic signals until a banner appears or timeout.
    # The expensive heuristic + LLM detection runs ONCE after the poll.
    baseline_len = await _get_html_length(session)
    logger.log("banner_poll_start", baseline_chars=baseline_len)

    signal_found, signal_type = await _poll_for_banner_signal(
        session, baseline_len, logger
    )
    if signal_found:
        await asyncio.sleep(_SETTLE_S)   # let fade-in animations finish
    logger.log(
        "banner_poll_done",
        signal_found=signal_found,
        signal_type=signal_type,
        waited_ms=int((time.monotonic() - t0) * 1000),
    )

    # ── _make_result closure (used by all paths below) ────────────────────────
    def _make_result(**kwargs) -> CloseResult:
        r = CloseResult(
            cost_estimate_eur=total_cost,
            duration_seconds=time.monotonic() - t0,
            log_dir=str(logger.log_dir),
            **kwargs,
        )
        logger.log("cookie_closer_result", **_result_fields(r))
        return r

    # ── Heuristic path (no LLM) ───────────────────────────────────────────────
    if heuristic_only:
        if not signal_found:
            # Even without a poll signal, verify no overlay is present.
            overlay_present, _ = await _check_overlay_present(session)
            if overlay_present:
                logger.log("heuristic_no_signal_but_overlay")
                clicked = await _close_banner_heuristic(session, logger)
                return _make_result(
                    success=clicked,
                    action="clicked" if clicked else "failed",
                    attempts=1,
                    error=None if clicked else "Heuristic found no accept button (overlay present)",
                )
            return _make_result(success=True, action="no_banner_found", attempts=0)
        clicked = await _close_banner_heuristic(session, logger)
        return _make_result(
            success=clicked,
            action="clicked" if clicked else "failed",
            attempts=1,
            error=None if clicked else "Heuristic found no accept button",
        )

    # ── LLM detection ────────────────────────────────────────────────────────
    last_click_selector: str | None = None
    last_click_selector_type: str | None = None
    last_click_rationale: str | None = None
    last_click_attempt: int = 0

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        html = await session.get_html()
        label = "cc_initial" if attempt == 1 else f"cc_attempt_{attempt}"
        snap = logger.save_snapshot(html, label)
        logger.log("html_captured", snapshot_file=snap, size_bytes=len(html.encode("utf-8")))

        # Live DOM modal candidates (dialog/aria-modal/role=dialog + z-index overlays)
        # are collected first and merged ahead of HTML-scored candidates so the LLM
        # always sees semantic dialog elements even when the HTML scorer misranks them.
        live_modals = await _collect_live_modal_candidates(session)
        if live_modals:
            logger.log("live_modal_candidates_found", count=len(live_modals))
        html_candidates = extract_banner_candidates(html)
        existing_html = {c["html"] for c in html_candidates}
        candidates = (
            [c for c in live_modals if c["html"] not in existing_html] + html_candidates
        )
        candidates = candidates[:max(3, len(live_modals) + 1)]

        if not candidates:
            logger.log("banner_not_detected", attempt=attempt)
            if last_click_selector:
                return _make_result(
                    success=True,
                    action="clicked",
                    selector=last_click_selector,
                    selector_type=last_click_selector_type,
                    rationale=last_click_rationale,
                    attempts=last_click_attempt,
                )
            # No scored candidates — verify no overlay is blocking before concluding.
            overlay_present, overlay_candidate = await _check_overlay_present(session)
            if overlay_present and overlay_candidate:
                logger.log("overlay_detected_despite_no_candidates", attempt=attempt)
                candidates = [overlay_candidate]
                # Fall through to LLM call with the overlay as sole candidate.
            else:
                return _make_result(success=True, action="no_banner_found", attempts=attempt)

        logger.log("banner_detected", attempt=attempt, candidates_count=len(candidates))

        try:
            decision, cost_eur = await ask_llm(candidates)
        except Exception as exc:
            logger.log("error", message=str(exc), attempt=attempt)
            return _make_result(
                success=False,
                action="failed",
                attempts=attempt,
                error=f"LLM error: {exc}",
            )

        total_cost += cost_eur
        call_id = logger.save_llm_call(candidates, decision, cost_eur)
        logger.log(
            "llm_call",
            call_id=call_id,
            input_file=f"call_{call_id}_input.html",
            output_file=f"call_{call_id}_output.json",
            tokens_in=decision.tokens_input,
            tokens_out=decision.tokens_output,
            cost_eur=cost_eur,
        )

        if decision.selector is None:
            if last_click_selector:
                logger.log("banner_gone_inferred", rationale=decision.rationale, attempt=attempt)
                return _make_result(
                    success=True,
                    action="clicked",
                    selector=last_click_selector,
                    selector_type=last_click_selector_type,
                    rationale=last_click_rationale,
                    attempts=last_click_attempt,
                )
            # LLM said no banner — verify no overlay is still blocking the viewport.
            overlay_present, _ = await _check_overlay_present(session)
            if overlay_present:
                logger.log("llm_no_banner_but_overlay_present",
                           rationale=decision.rationale, attempt=attempt)
                if attempt < _MAX_ATTEMPTS:
                    continue  # retry: live candidates re-collected next iteration
                return _make_result(
                    success=False,
                    action="failed",
                    attempts=attempt,
                    error=f"Overlay blocks viewport but LLM found no banner: {decision.rationale}",
                )
            logger.log("no_banner_found", rationale=decision.rationale, attempt=attempt)
            return _make_result(
                success=True,
                action="no_banner_found",
                rationale=decision.rationale,
                attempts=attempt,
            )

        clicked = await session.click_selector(decision.selector, decision.selector_type)

        if clicked:
            logger.log("click", selector=decision.selector,
                       selector_type=decision.selector_type, success=True)
            last_click_selector = decision.selector
            last_click_selector_type = decision.selector_type
            last_click_rationale = decision.rationale
            last_click_attempt = attempt

            await asyncio.sleep(0.5)
            still_visible = await session.is_visible(decision.selector, decision.selector_type)
            if not still_visible:
                logger.log("banner_cleared_visibility_check", attempt=attempt)
                return _make_result(
                    success=True,
                    action="clicked",
                    selector=decision.selector,
                    selector_type=decision.selector_type,
                    rationale=decision.rationale,
                    attempts=attempt,
                )

            html_after = await session.get_html()
            snap_after = logger.save_snapshot(html_after, f"cc_after_click_{attempt}")
            logger.log("html_captured", snapshot_file=snap_after,
                       size_bytes=len(html_after.encode("utf-8")))
            logger.log("banner_still_visible_after_click", attempt=attempt)
        else:
            logger.log("click_failed", selector=decision.selector, attempt=attempt)

    return _make_result(
        success=False,
        action="failed",
        attempts=_MAX_ATTEMPTS,
        error=f"Banner still present after {_MAX_ATTEMPTS} attempts",
    )


async def close_cookies(
    url: str,
    site_name: str,
    headless: bool = True,
) -> CloseResult:
    """
    Standalone entry point: create a browser session, navigate to *url*,
    then close the cookie banner.

    Creates its own log directory under logs/<timestamp>_<site_name>/.
    Returns a CloseResult including the log_dir path.
    """
    logger = SessionLogger(site_name)
    started_at = logger.now()

    logger.log("navigate", url=url)
    nav_t0 = time.monotonic()
    async with BrowserSession(headless=headless) as session:
        await session.navigate(url)
        logger.log("navigate_complete", url=url,
                   duration_ms=int((time.monotonic() - nav_t0) * 1000))
        result = await close_cookies_in_session(session, logger.log_dir)

    logger.write_summary(result, site_name, url, started_at)
    return result
