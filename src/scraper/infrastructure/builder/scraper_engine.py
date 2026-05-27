"""
scraper_engine — Fase B

Single entry point:  scrape(url, targets, log_dir) -> ScrapeResult

Orchestrates the full discovery flow in one browser session:
  cookies → form → submit → results → scroll → dual-path extraction → verify

All building blocks are reused as-is; only the glue lives here.
Provider-agnostic: no hardcoded selectors, provider names, or CSS classes.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from anthropic import AsyncAnthropic

from .browser_session import BrowserSession
from .extraction.container_extractor import extract_results_container_html
from .extraction.extraction_verifier import VerificationResult, verify
from .extraction.models import ResultsStructure, VehicleResult
from .extraction.results_classifier import classify_results_structure
from .extraction.results_extractor_dom import extract_vehicles_dom
from .extraction.results_extractor_llm import extract_vehicles_llm
from .field_analysis.field_identifier import FormFields
from .form_capture.html_cleaner import clean_html_for_llm
from .form_fill_orchestrator import fill_form_in_session
from .results_detection.results_waiter import wait_for_results
from .results_detection.scroll_loader import ensure_all_results_loaded
from .session_logger import SessionLogger


@dataclass
class ScrapeResult:
    url: str
    targets: dict
    success: bool
    failed_phase: str | None        # "cookies"|"form"|"submit"|"results"|"extraction"|None
    vehicles: list[VehicleResult]   # LLM path — reference truth
    dom_vehicles: list[VehicleResult]   # DOM path — for recipe / verification
    # Discovery artifacts for Fase C:
    form_fields: FormFields | None
    results_structure: ResultsStructure | None
    verification: VerificationResult | None
    # Metrics:
    duration_seconds: float
    cost_estimate_eur: float
    llm_calls: int
    scroll_rounds: int
    scroll_final_count: int
    error: str | None
    # True when results_waiter detected a "no availability" page body message.
    # Used by check_recipe_health to distinguish legit empty results from breakage.
    has_empty_page: bool = False


async def scrape(
    url: str,
    targets: dict,
    log_dir: Path,
    headless: bool = True,
) -> ScrapeResult:
    """
    Full discovery scrape in a single browser session.

    Phase sequence:
      1. form     — navigate, close cookie banner, fill all fields
      2. submit   — click the submit button
      3. results  — wait for the results page to load
      (scroll)    — ensure all lazy-loaded cards are rendered
      4. extraction — dual-path: LLM (reference) + DOM (selector-based)
      (verify)    — compare both paths

    On phase failure: sets failed_phase, success=False, returns a partial
    ScrapeResult with whatever has been accumulated so far.  The browser
    session is always closed (async context manager).
    """
    t0 = time.monotonic()
    total_cost = 0.0
    llm_calls = 0
    scroll_rounds = 0
    scroll_final_count = 0
    has_empty_page = False
    form_fields: FormFields | None = None
    results_structure: ResultsStructure | None = None
    vehicles: list[VehicleResult] = []
    dom_vehicles: list[VehicleResult] = []
    verification: VerificationResult | None = None

    client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    logger = SessionLogger(log_dir)

    def _elapsed() -> float:
        return time.monotonic() - t0

    def _make_result(
        failed_phase: str | None,
        error: str | None,
        success: bool = False,
    ) -> ScrapeResult:
        logger.log(
            "scrape_complete",
            success=success,
            failed_phase=failed_phase,
            duration_s=round(_elapsed(), 2),
            cost_eur=round(total_cost, 6),
            llm_calls=llm_calls,
            vehicles=len(vehicles),
            scroll_rounds=scroll_rounds,
            scroll_final_count=scroll_final_count,
        )
        return ScrapeResult(
            url=url,
            targets=targets,
            success=success,
            failed_phase=failed_phase,
            error=error,
            vehicles=vehicles,
            dom_vehicles=dom_vehicles,
            form_fields=form_fields,
            results_structure=results_structure,
            verification=verification,
            duration_seconds=_elapsed(),
            cost_estimate_eur=total_cost,
            llm_calls=llm_calls,
            scroll_rounds=scroll_rounds,
            scroll_final_count=scroll_final_count,
            has_empty_page=has_empty_page,
        )

    try:
        async with BrowserSession(headless=headless) as session:

            # ── Phase: form (navigate + cookies + fill) ───────────────────────
            try:
                form_report, form_fields = await fill_form_in_session(
                    session, url, targets, log_dir, client
                )
                total_cost += form_report.cost_estimate_eur
                llm_calls += form_report.llm_calls
            except Exception as exc:
                logger.log("scrape_phase_error", phase="form", error=str(exc))
                return _make_result("form", f"Form phase exception: {exc}")

            if not form_report.success:
                failed_phase = "cookies" if form_report.failed_at == "cookies" else "form"
                return _make_result(failed_phase, form_report.error or f"{failed_phase} failed")

            if form_fields is None or form_fields.submit_button is None:
                return _make_result("form", "No submit button identified")

            # ── Phase: submit ─────────────────────────────────────────────────
            url_before = session.get_url()
            try:
                clicked = await session.click_selector(
                    form_fields.submit_button.selector,
                    form_fields.submit_button.selector_type,
                )
                logger.log(
                    "submit_clicked",
                    selector=form_fields.submit_button.selector,
                    success=clicked,
                )
                if not clicked:
                    return _make_result(
                        "submit",
                        f"Could not click submit: {form_fields.submit_button.selector!r}",
                    )
            except Exception as exc:
                logger.log("scrape_phase_error", phase="submit", error=str(exc))
                return _make_result("submit", f"Submit exception: {exc}")

            # ── Phase: results ────────────────────────────────────────────────
            try:
                wait_outcome = await wait_for_results(session, url_before, logger)
                logger.log(
                    "results_waited",
                    ready=wait_outcome.ready,
                    signal=wait_outcome.signal,
                    waited_ms=wait_outcome.waited_ms,
                    candidate_count=wait_outcome.candidate_count,
                )
                has_empty_page = wait_outcome.signal == "empty_message"
                if not wait_outcome.ready:
                    return _make_result(
                        "results",
                        f"Results page did not load (signal={wait_outcome.signal})",
                    )
            except Exception as exc:
                logger.log("scrape_phase_error", phase="results", error=str(exc))
                return _make_result("results", f"Results wait exception: {exc}")

            # ── Scroll-to-complete (non-fatal) ────────────────────────────────
            try:
                scroll_final_count, scroll_rounds = await ensure_all_results_loaded(
                    session, logger
                )
                logger.log(
                    "scroll_summary",
                    rounds=scroll_rounds,
                    final_count=scroll_final_count,
                )
            except Exception as exc:
                logger.log("scroll_error", error=str(exc))

            # ── Container isolation (non-fatal) ───────────────────────────────
            container_html: str | None = None
            try:
                container_html = await extract_results_container_html(session)
                logger.log(
                    "container_extracted",
                    chars=len(container_html) if container_html else 0,
                )
                if container_html:
                    (log_dir / "dom_snapshots" / "results_container.html").write_text(
                        clean_html_for_llm(container_html, preserve_svg_aria=True),
                        encoding="utf-8",
                    )
            except Exception as exc:
                logger.log("container_error", error=str(exc))

            results_html = await session.get_html()
            cleaned_html = clean_html_for_llm(results_html)
            (log_dir / "dom_snapshots" / "results_full.html").write_text(
                cleaned_html, encoding="utf-8"
            )
            logger.log("results_reached", reached=True, html_chars=len(cleaned_html))

            # ── Phase: extraction — LLM path (reference truth) ────────────────
            try:
                vehicles, llm_cost = await extract_vehicles_llm(
                    results_html, logger, client, container_html=container_html
                )
                total_cost += llm_cost
                llm_calls += 1
            except Exception as exc:
                logger.log("llm_extraction_error", error=str(exc))
                vehicles = []

            # ── Phase: extraction — DOM path (for recipe / verification) ──────
            try:
                results_structure, struct_cost = await classify_results_structure(
                    results_html, logger, client, container_html=container_html
                )
                total_cost += struct_cost
                llm_calls += 1

                (log_dir / "dom_snapshots" / "structure.json").write_text(
                    json.dumps(
                        {
                            "vehicle_card_selector": results_structure.vehicle_card_selector,
                            "field_selectors": [
                                fs.__dict__ for fs in results_structure.field_selectors
                            ],
                            "price_strategy": results_structure.price_strategy,
                            "rationale": results_structure.rationale,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )

                dom_vehicles = await extract_vehicles_dom(session, results_structure, logger)
            except Exception as exc:
                logger.log("structure_or_dom_error", error=str(exc))

            # ── Verify ────────────────────────────────────────────────────────
            if vehicles:
                verification = verify(vehicles, dom_vehicles)
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
                        ensure_ascii=False,
                        indent=2,
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

            if not vehicles:
                if has_empty_page:
                    return _make_result(None, None, success=True)
                return _make_result("extraction", "LLM extraction returned no vehicles")

            success = verification is not None and verification.match
            error: str | None = None
            if not success:
                if verification is None:
                    error = "LLM extraction succeeded but verification was skipped"
                else:
                    error = f"Verification failed: {verification.rationale}"

            return _make_result(None, error, success=success)

    except Exception as exc:
        logger.log("scrape_unexpected_error", error=str(exc))
        return _make_result(None, f"Unexpected error: {exc}")
