"""Orchestrates banner detection → LLM decision → click → verify."""
from __future__ import annotations

import time

from banner_detector import extract_banner_candidates
from browser_session import BrowserSession
from llm_selector import ask_llm
from models import CloseResult

_MAX_ATTEMPTS = 3


async def close_cookies(session: BrowserSession, url: str) -> CloseResult:
    """
    Navigate to url and attempt to close the cookie banner.
    Returns a CloseResult describing the outcome.
    """
    t0 = time.monotonic()
    total_cost = 0.0

    await session.navigate(url)

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        html = await session.get_html()
        candidates = extract_banner_candidates(html)

        if not candidates:
            return CloseResult(
                success=True,
                action="no_banner_found",
                attempts=attempt,
                cost_estimate_eur=total_cost,
                duration_seconds=time.monotonic() - t0,
            )

        try:
            decision, cost_eur = await ask_llm(candidates)
        except Exception as exc:
            return CloseResult(
                success=False,
                action="failed",
                attempts=attempt,
                cost_estimate_eur=total_cost,
                duration_seconds=time.monotonic() - t0,
                error=f"LLM error: {exc}",
            )

        total_cost += cost_eur

        if decision.selector is None:
            return CloseResult(
                success=True,
                action="no_banner_found",
                rationale=decision.rationale,
                attempts=attempt,
                cost_estimate_eur=total_cost,
                duration_seconds=time.monotonic() - t0,
            )

        clicked = await session.click_selector(decision.selector, decision.selector_type)
        if clicked:
            # Verify banner is gone on next HTML snapshot
            html_after = await session.get_html()
            remaining = extract_banner_candidates(html_after)
            if not remaining:
                return CloseResult(
                    success=True,
                    action="clicked",
                    selector=decision.selector,
                    selector_type=decision.selector_type,
                    rationale=decision.rationale,
                    attempts=attempt,
                    cost_estimate_eur=total_cost,
                    duration_seconds=time.monotonic() - t0,
                )
            # Banner still present — loop for another attempt

    return CloseResult(
        success=False,
        action="failed",
        attempts=_MAX_ATTEMPTS,
        cost_estimate_eur=total_cost,
        duration_seconds=time.monotonic() - t0,
        error=f"Banner still present after {_MAX_ATTEMPTS} attempts",
    )
