"""Orchestrate: reach results → extract (two paths) → verify.

Delegates to scraper_engine.scrape() for the full flow.
ExtractionExperimentReport is preserved for run_extract.py compatibility.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from extraction.extraction_verifier import VerificationResult
from extraction.models import ResultsStructure, VehicleResult
from scraper_engine import ScrapeResult, scrape


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
    result: ScrapeResult = await scrape(url, targets, log_dir, headless=headless)

    # reached_results: got past navigation/form/submit/wait phases
    reached = result.failed_phase not in ("cookies", "form", "submit", "results")

    return ExtractionExperimentReport(
        site_url=result.url,
        targets=result.targets,
        reached_results=reached,
        llm_vehicles=result.vehicles,
        structure=result.results_structure,
        dom_vehicles=result.dom_vehicles,
        verification=result.verification,
        success=result.success,
        duration_seconds=result.duration_seconds,
        cost_estimate_eur=result.cost_estimate_eur,
        llm_calls=result.llm_calls,
        error=result.error,
        scroll_rounds=result.scroll_rounds,
        scroll_final_count=result.scroll_final_count,
    )
