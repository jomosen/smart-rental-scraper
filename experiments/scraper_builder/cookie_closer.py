"""
Cookie-banner closer — two entry points:

- close_cookies_in_session(session, log_dir): assumes page already loaded,
  shares the caller's log directory.
- close_cookies(url, site_name, headless): standalone; creates its own
  browser session, navigates, then calls close_cookies_in_session.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from banner_detector import extract_banner_candidates
from browser_session import BrowserSession
from llm_selector import ask_llm
from models import CloseResult
from session_logger import SessionLogger

_MAX_ATTEMPTS = 3

# CSS selector covering the most common CMP patterns.
# Used to wait for JS-rendered banners before attempting detection.
_BANNER_HINTS = ", ".join([
    "dialog[role='dialog']",
    "[data-cookiefirst-action]",
    "#onetrust-consent-sdk",
    "#cookiebanner",
    "[id*='cookiebot']",
    "[class*='cookie-banner']",
    "[class*='cookie-consent']",
    "[aria-label*='cookie' i]",
    "[aria-label*='cookies' i]",
])


def _result_fields(r: CloseResult) -> dict:
    return {"success": r.success, "action": r.action, "selector": r.selector, "attempts": r.attempts}


async def close_cookies_in_session(
    session: BrowserSession,
    log_dir: Path,
) -> CloseResult:
    """
    Close the cookie banner on the currently loaded page.

    Does not navigate — assumes the caller already loaded the page.
    Appends events to log_dir/trace.jsonl and saves snapshots/llm_calls
    into the same log_dir, making all logs inspectable in one place.
    """
    logger = SessionLogger(log_dir)   # reuses existing dir; counters reset to 0
    t0 = time.monotonic()
    total_cost = 0.0

    # Wait for JS-rendered CMPs (e.g. CookieFirst ES module) to inject the banner.
    # domcontentloaded fires before async modules finish — without this wait the
    # snapshot may not contain the banner element at all.
    appeared = await session.wait_for_selector(_BANNER_HINTS, timeout_ms=8_000)
    logger.log("banner_wait", appeared=appeared,
               waited_ms=int((time.monotonic() - t0) * 1000))
    if appeared:
        # Brief pause for fade-in animations so the element is clickable
        await asyncio.sleep(0.3)

    last_click_selector: str | None = None
    last_click_selector_type: str | None = None
    last_click_rationale: str | None = None
    last_click_attempt: int = 0

    def _make_result(**kwargs) -> CloseResult:
        r = CloseResult(
            cost_estimate_eur=total_cost,
            duration_seconds=time.monotonic() - t0,
            log_dir=str(logger.log_dir),
            **kwargs,
        )
        logger.log("cookie_closer_result", **_result_fields(r))
        return r

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        html = await session.get_html()
        label = "cc_initial" if attempt == 1 else f"cc_attempt_{attempt}"
        snap = logger.save_snapshot(html, label)
        logger.log("html_captured", snapshot_file=snap, size_bytes=len(html.encode("utf-8")))

        candidates = extract_banner_candidates(html)

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
            logger.log("no_banner_found", rationale=decision.rationale, attempt=attempt)
            return _make_result(
                success=True,
                action="no_banner_found",
                rationale=decision.rationale,
                attempts=attempt,
            )

        clicked = await session.click_selector(decision.selector, decision.selector_type)

        if clicked:
            logger.log("click", selector=decision.selector, selector_type=decision.selector_type, success=True)
            last_click_selector = decision.selector
            last_click_selector_type = decision.selector_type
            last_click_rationale = decision.rationale
            last_click_attempt = attempt

            html_after = await session.get_html()
            snap_after = logger.save_snapshot(html_after, f"cc_after_click_{attempt}")
            logger.log("html_captured", snapshot_file=snap_after, size_bytes=len(html_after.encode("utf-8")))

            remaining = extract_banner_candidates(html_after)
            if not remaining:
                logger.log("banner_cleared", attempt=attempt)
                return _make_result(
                    success=True,
                    action="clicked",
                    selector=decision.selector,
                    selector_type=decision.selector_type,
                    rationale=decision.rationale,
                    attempts=attempt,
                )
            logger.log(
                "banner_still_present_after_click",
                attempt=attempt,
                remaining_candidates=len(remaining),
            )
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
