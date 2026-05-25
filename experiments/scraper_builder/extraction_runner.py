"""Orchestrate: reach results → extract (two paths) → verify."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from anthropic import AsyncAnthropic

from browser_session import BrowserSession
from extraction.extraction_verifier import VerificationResult, verify
from extraction.models import ResultsStructure, VehicleResult
from extraction.results_classifier import classify_results_structure
from extraction.results_extractor_dom import extract_vehicles_dom
from extraction.results_extractor_llm import extract_vehicles_llm
from form_capture.html_cleaner import clean_html_for_llm
from form_fill_orchestrator import fill_form_in_session
from results_detection.results_waiter import wait_for_results
from results_detection.scroll_loader import ensure_all_results_loaded
from session_logger import SessionLogger


@dataclass
class ExtractionExperimentReport:
    site_url: str
    targets: dict
    reached_results: bool
    llm_vehicles: list[VehicleResult]
    structure: ResultsStructure | None
    dom_vehicles: list[VehicleResult]
    verification: VerificationResult | None
    success: bool            # reached_results AND verification.match
    duration_seconds: float
    cost_estimate_eur: float
    llm_calls: int
    error: str | None
    scroll_rounds: int = 0
    scroll_final_count: int = 0


async def extract_experiment(
    url: str,
    targets: dict,
    log_dir: Path,
    headless: bool = True,
) -> ExtractionExperimentReport:
    """
    Full flow:
    1. Fill form in session.
    2. Click submit, wait for results page.
    3. Capture results HTML.
    4. Vía 1 (LLM): extract all vehicles as JSON (reference truth).
    5. Vía 2 (DOM): classify structure with LLM, then apply selectors deterministically.
    6. Verify: compare both paths.
    """
    t0 = time.monotonic()
    total_cost = 0.0
    llm_calls = 0

    client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    logger = SessionLogger(log_dir)

    def _elapsed() -> float:
        return time.monotonic() - t0

    def _fail(msg: str, **kw) -> ExtractionExperimentReport:
        return ExtractionExperimentReport(
            site_url=url, targets=targets,
            reached_results=False,
            llm_vehicles=[], structure=None, dom_vehicles=[],
            verification=None, success=False,
            duration_seconds=_elapsed(),
            cost_estimate_eur=total_cost, llm_calls=llm_calls,
            error=msg,
        )

    async with BrowserSession(headless=headless) as session:
        # ── 1. Fill form ──────────────────────────────────────────────────────
        form_report, fields = await fill_form_in_session(
            session, url, targets, log_dir, client
        )
        total_cost += form_report.cost_estimate_eur
        llm_calls += form_report.llm_calls

        if not form_report.success:
            return _fail(form_report.error or "Form fill failed")

        if fields is None or fields.submit_button is None:
            return _fail("No submit button identified")

        # ── 2. Submit ─────────────────────────────────────────────────────────
        url_before = session.get_url()
        clicked = await session.click_selector(
            fields.submit_button.selector, fields.submit_button.selector_type
        )
        logger.log("submit_clicked", selector=fields.submit_button.selector, success=clicked)

        if not clicked:
            return _fail(f"Could not click submit: {fields.submit_button.selector!r}")

        # ── 3. Wait for results ───────────────────────────────────────────────
        wait_outcome = await wait_for_results(session, url_before, logger)
        logger.log(
            "results_waited",
            ready=wait_outcome.ready,
            signal=wait_outcome.signal,
            waited_ms=wait_outcome.waited_ms,
            candidate_count=wait_outcome.candidate_count,
        )
        reached = wait_outcome.ready

        # ── Scroll-to-complete: ensure the full list is rendered ──────────────
        scroll_final_count = 0
        scroll_rounds = 0
        if reached:
            scroll_final_count, scroll_rounds = await ensure_all_results_loaded(
                session, logger
            )
            logger.log(
                "scroll_summary",
                rounds=scroll_rounds,
                final_count=scroll_final_count,
            )

        # Capture HTML regardless of ready state (best-effort extraction)
        results_html = await session.get_html()
        cleaned_html = clean_html_for_llm(results_html)
        (log_dir / "dom_snapshots" / "results_full.html").write_text(
            cleaned_html, encoding="utf-8"
        )
        logger.log("results_reached", reached=reached, html_chars=len(cleaned_html))

        if not reached:
            return ExtractionExperimentReport(
                site_url=url, targets=targets,
                reached_results=False,
                llm_vehicles=[], structure=None, dom_vehicles=[],
                verification=None, success=False,
                duration_seconds=_elapsed(),
                cost_estimate_eur=total_cost, llm_calls=llm_calls,
                error=f"Results page did not load (signal={wait_outcome.signal})",
            )

        # ── 4. Vía 1: LLM extracts all vehicles (reference truth) ─────────────
        try:
            llm_vehicles, llm_cost = await extract_vehicles_llm(
                results_html, logger, client
            )
            total_cost += llm_cost
            llm_calls += 1
        except Exception as exc:
            logger.log("llm_extraction_error", error=str(exc))
            llm_vehicles, llm_cost = [], 0.0

        # ── 5. Vía 2a: LLM classifies structure ──────────────────────────────
        structure: ResultsStructure | None = None
        dom_vehicles: list[VehicleResult] = []

        try:
            structure, struct_cost = await classify_results_structure(
                results_html, logger, client
            )
            total_cost += struct_cost
            llm_calls += 1

            (log_dir / "dom_snapshots" / "structure.json").write_text(
                json.dumps(
                    {
                        "vehicle_card_selector": structure.vehicle_card_selector,
                        "field_selectors": [fs.__dict__ for fs in structure.field_selectors],
                        "price_strategy": structure.price_strategy,
                        "rationale": structure.rationale,
                    },
                    ensure_ascii=False, indent=2,
                ),
                encoding="utf-8",
            )

            # ── 5b: DOM extraction (deterministic) ───────────────────────────
            dom_vehicles = await extract_vehicles_dom(session, structure, logger)

        except Exception as exc:
            logger.log("structure_or_dom_error", error=str(exc))

        # ── 6. Verify ─────────────────────────────────────────────────────────
        verification: VerificationResult | None = None
        if llm_vehicles:
            verification = verify(llm_vehicles, dom_vehicles)
            (log_dir / "dom_snapshots" / "verification.json").write_text(
                json.dumps(
                    {
                        "match": verification.match,
                        "llm_count": verification.llm_count,
                        "dom_count": verification.dom_count,
                        "field_agreement": verification.field_agreement,
                        "mismatches": verification.mismatches,
                        "rationale": verification.rationale,
                    },
                    ensure_ascii=False, indent=2,
                ),
                encoding="utf-8",
            )
            logger.log(
                "verification",
                match=verification.match,
                llm_count=verification.llm_count,
                dom_count=verification.dom_count,
                mismatches=len(verification.mismatches),
            )

        success = reached and (verification is not None and verification.match)
        error: str | None = None
        if not success:
            if not reached:
                error = "Did not reach results page"
            elif verification is None:
                error = "LLM extraction failed — nothing to verify"
            else:
                error = f"Verification failed: {verification.rationale}"

        return ExtractionExperimentReport(
            site_url=url, targets=targets,
            reached_results=reached,
            llm_vehicles=llm_vehicles,
            structure=structure,
            dom_vehicles=dom_vehicles,
            verification=verification,
            success=success,
            duration_seconds=_elapsed(),
            cost_estimate_eur=total_cost,
            llm_calls=llm_calls,
            error=error,
            scroll_rounds=scroll_rounds,
            scroll_final_count=scroll_final_count,
        )
