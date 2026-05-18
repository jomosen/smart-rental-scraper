"""Orchestrates banner detection → LLM decision → click → verify."""
from __future__ import annotations

import time

from banner_detector import extract_banner_candidates
from browser_session import BrowserSession
from llm_selector import ask_llm
from models import CloseResult
from session_logger import SessionLogger

_MAX_ATTEMPTS = 3


def _result_fields(r: CloseResult) -> dict:
    return {"success": r.success, "action": r.action, "selector": r.selector, "attempts": r.attempts}


async def close_cookies(session: BrowserSession, url: str, site_name: str) -> CloseResult:
    """
    Navigate to url and attempt to close the cookie banner.
    Persists an audit log under logs/<timestamp>_<site_name>/.
    Returns a CloseResult describing the outcome.
    """
    logger = SessionLogger(site_name)
    started_at = logger.now()
    t0 = time.monotonic()
    total_cost = 0.0

    # Track the last successful click so a false-positive re-detection in a
    # subsequent verification cycle does not overwrite the real outcome.
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
        logger.log("result", **_result_fields(r))
        logger.write_summary(r, site_name, url, started_at)
        return r

    logger.log("navigate", url=url)
    nav_t0 = time.monotonic()
    await session.navigate(url)
    logger.log("navigate_complete", url=url, duration_ms=int((time.monotonic() - nav_t0) * 1000))

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        html = await session.get_html()
        label = "initial" if attempt == 1 else f"attempt_{attempt}_start"
        snap = logger.save_snapshot(html, label)
        logger.log("html_captured", snapshot_file=snap, size_bytes=len(html.encode("utf-8")))

        candidates = extract_banner_candidates(html)

        if not candidates:
            logger.log("banner_not_detected", attempt=attempt)
            if last_click_selector:
                # A click was performed in a prior cycle and the banner is now gone.
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
                # LLM finds no button to click — banner must be gone.
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
            snap_after = logger.save_snapshot(html_after, f"after_click_{attempt}")
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
