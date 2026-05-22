"""Experiment 7: fill form → submit → detect results page."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from anthropic import AsyncAnthropic

from browser_session import BrowserSession
from form_fill_orchestrator import fill_form_in_session
from results_detection.results_confirmer import ResultsConfirmation, confirm_results
from results_detection.results_waiter import WaitOutcome, wait_for_results
from session_logger import SessionLogger


@dataclass
class SubmitExperimentReport:
    site_url: str
    targets: dict
    form_filled: bool
    submit_clicked: bool
    wait_outcome: WaitOutcome | None
    confirmation: ResultsConfirmation | None
    success: bool
    duration_seconds: float
    cost_estimate_eur: float
    llm_calls: int
    error: str | None


async def submit_and_detect(
    url: str,
    targets: dict,
    log_dir: Path,
    headless: bool = True,
) -> SubmitExperimentReport:
    """
    Fill the search form, click submit, wait for results, confirm with LLM.

    The browser session is created internally; the caller only provides
    connection parameters and gets back a structured report.
    """
    t0 = time.monotonic()
    total_cost = 0.0
    llm_calls = 0

    client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    logger = SessionLogger(log_dir)

    def _elapsed() -> float:
        return time.monotonic() - t0

    async with BrowserSession(headless=headless) as session:
        # ── 1. Fill form ──────────────────────────────────────────────────────
        form_report, fields = await fill_form_in_session(
            session, url, targets, log_dir, client
        )
        total_cost += form_report.cost_estimate_eur
        llm_calls += form_report.llm_calls

        if not form_report.success:
            return SubmitExperimentReport(
                site_url=url, targets=targets,
                form_filled=False, submit_clicked=False,
                wait_outcome=None, confirmation=None,
                success=False,
                duration_seconds=_elapsed(),
                cost_estimate_eur=total_cost,
                llm_calls=llm_calls,
                error=form_report.error or "Form fill failed",
            )

        if fields is None or fields.submit_button is None:
            return SubmitExperimentReport(
                site_url=url, targets=targets,
                form_filled=True, submit_clicked=False,
                wait_outcome=None, confirmation=None,
                success=False,
                duration_seconds=_elapsed(),
                cost_estimate_eur=total_cost,
                llm_calls=llm_calls,
                error="No submit button identified by field analysis",
            )

        # ── 2. Record URL before submit ───────────────────────────────────────
        url_before = session.get_url()

        # ── 3. Click submit ───────────────────────────────────────────────────
        submit_btn = fields.submit_button
        clicked = await session.click_selector(
            submit_btn.selector, submit_btn.selector_type
        )
        logger.log(
            "submit_clicked",
            selector=submit_btn.selector,
            selector_type=submit_btn.selector_type,
            success=clicked,
        )

        if not clicked:
            return SubmitExperimentReport(
                site_url=url, targets=targets,
                form_filled=True, submit_clicked=False,
                wait_outcome=None, confirmation=None,
                success=False,
                duration_seconds=_elapsed(),
                cost_estimate_eur=total_cost,
                llm_calls=llm_calls,
                error=f"Could not click submit button: {submit_btn.selector!r}",
            )

        # ── 4. Wait for results (heuristic) ───────────────────────────────────
        wait_outcome = await wait_for_results(session, url_before, logger)
        logger.log(
            "results_waited",
            ready=wait_outcome.ready,
            signal=wait_outcome.signal,
            waited_ms=wait_outcome.waited_ms,
            candidate_count=wait_outcome.candidate_count,
            url_after=wait_outcome.url_after,
        )

        # ── 5. Confirm with LLM (one call) ────────────────────────────────────
        confirmation, confirm_cost = await confirm_results(session, logger, client)
        total_cost += confirm_cost
        llm_calls += 1
        logger.log(
            "results_confirmed",
            page_type=confirmation.page_type,
            approx_vehicle_count=confirmation.approx_vehicle_count,
            rationale=confirmation.rationale,
        )

        success = (
            wait_outcome.ready
            and confirmation.page_type in ("results", "empty")
        )
        error: str | None = None
        if not success:
            if not wait_outcome.ready:
                error = f"Results did not load (signal={wait_outcome.signal})"
            else:
                error = f"Unexpected page type: {confirmation.page_type!r}"

        return SubmitExperimentReport(
            site_url=url, targets=targets,
            form_filled=True, submit_clicked=True,
            wait_outcome=wait_outcome, confirmation=confirmation,
            success=success,
            duration_seconds=_elapsed(),
            cost_estimate_eur=total_cost,
            llm_calls=llm_calls,
            error=error,
        )
