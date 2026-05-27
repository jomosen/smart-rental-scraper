"""
RecipeScraper — execute a Recipe with ZERO LLM calls.

run_recipe() reuses the same Playwright fillers and DOM extractor as scrape()
but feeds them from the recipe instead of from LLM classification.  The only
structural difference from scrape():

  * Cookie closing: heuristic text-based accept button (no ask_llm).
  * Form filling:   widget type + selectors come from Recipe, no classify_widget /
                    classify_date_widget.
  * Extraction:     DOM path only (extract_vehicles_dom); no LLM extractor, no
                    classify_results_structure.  card_source="mark_valid_cards"
                    forces the heading+price heuristic (Pass 2 in _MARK_CARDS_JS).

llm_calls in the returned ScrapeResult must be 0.  If it is > 0, that is a bug.
"""
from __future__ import annotations

import asyncio
import time
from datetime import date
from pathlib import Path

from .browser_session import BrowserSession
from .cookie_closer import close_cookies_in_session
from .date_analysis.date_widget_classifier import DateWidgetInfo
from .extraction.models import FieldSelector, ResultsStructure, VehicleResult
from .extraction.results_extractor_dom import extract_vehicles_dom
from .extraction.extraction_verifier import VerificationResult
from .field_analysis.field_identifier import FormFields, IdentifiedField
from .field_analysis.widget_classifier import WidgetInfo
from .filling.date_filler_factory import UnsupportedDateWidgetError, get_date_filler
from .filling.filler_factory import UnsupportedWidgetError, get_filler_for_widget
from .results_detection.results_waiter import wait_for_results
from .results_detection.scroll_loader import ensure_all_results_loaded
from .scraper_engine import ScrapeResult
from .session_logger import SessionLogger

from ...domain.builder.models import Recipe


# ── JS helpers (self-contained — no imports from form_fill_orchestrator) ──────

_DETECT_FORM_JS = """
() => {
    const forms = document.querySelectorAll("form");
    if (!forms.length) return "form";
    let best = null, bestCount = 0;
    for (const f of forms) {
        const n = f.querySelectorAll("input, select, textarea").length;
        if (n > bestCount) { bestCount = n; best = f; }
    }
    if (!best) return "form";
    return best.id ? "#" + best.id : "form";
}
"""


async def _detect_form_selector(session: BrowserSession) -> str:
    try:
        sel: str = await session.page.evaluate(_DETECT_FORM_JS)
        return sel or "form"
    except Exception:
        return "form"


async def _dismiss(session: BrowserSession) -> None:
    """Press Escape to close any open dropdown or calendar."""
    try:
        await session.page.keyboard.press("Escape")
        await asyncio.sleep(0.3)
    except Exception:
        pass


def _make_identified_field(rf) -> IdentifiedField:
    return IdentifiedField(
        selector=rf.selector,
        selector_type=rf.selector_type,
        element_kind=rf.element_kind,
        rationale="From recipe",
    )


def _make_widget_info(rf) -> WidgetInfo:
    return WidgetInfo(
        widget_type=rf.widget_type,
        options_container_selector=rf.options_container_selector,
        option_item_selector=rf.option_item_selector,
        is_searchable=rf.is_searchable if rf.is_searchable is not None else True,
        rationale="From recipe",
    )


def _make_date_widget_info(rf) -> DateWidgetInfo:
    return DateWidgetInfo(
        widget_type=rf.widget_type,
        accepts_direct_typing=rf.accepts_direct_typing or False,
        date_format=rf.date_format,
        calendar_container_selector=rf.calendar_container_selector,
        next_month_selector=rf.next_month_selector,
        prev_month_selector=rf.prev_month_selector,
        day_cell_selector=rf.day_cell_selector,
        month_year_label_selector=rf.month_year_label_selector,
        is_range_calendar=rf.is_range_calendar or False,
        rationale="From recipe",
    )


def _recipe_to_results_structure(recipe: Recipe) -> ResultsStructure:
    """
    Build a ResultsStructure for extract_vehicles_dom.

    vehicle_card_selector is intentionally set to a selector that matches
    nothing ("[data-recipe-heuristic]"), so Pass 1 yields 0 cards and the
    heuristic Pass 2 (heading+price walk-up, leaf filter) always fires.
    This is the "mark_valid_cards" strategy.

    All field_extractors are included — semantic ones (aria_keyword,
    aria_keyword_transmission, price_cascade) carry no selector but carry
    their keyword lists so the DOM extractor can dispatch correctly.
    """
    field_selectors = [
        FieldSelector(
            field=e.field,
            selector=e.selector or "",
            extraction=e.extraction,
            rationale="From recipe",
            keywords=e.keywords,
            auto_keywords=e.auto_keywords,
            manual_keywords=e.manual_keywords,
        )
        for e in recipe.field_extractors
    ]
    return ResultsStructure(
        vehicle_card_selector="[data-recipe-heuristic]",
        field_selectors=field_selectors,
        price_strategy=recipe.price_strategy,
        rationale="From recipe (card_source=mark_valid_cards)",
    )


async def _fill_recipe_fields(
    session: BrowserSession,
    recipe: Recipe,
    targets: dict,
    form_selector: str,
    logger: SessionLogger,
) -> None:
    """
    Fill each form field using data from the recipe.  No LLM.

    Fields are processed in order: location → pickup_date → return_date →
    pickup_time → return_time.  Fields with strategy="range_calendar_autofill"
    are skipped — the range calendar already set them during pickup_date fill.
    """
    pickup_date: date = targets["pickup_date"]
    return_date: date = targets["return_date"]
    field_targets: dict = {
        "pickup_location": targets["location"],
        "pickup_date":     pickup_date,
        "return_date":     return_date,
        "pickup_time":     targets["pickup_time"],
        "return_time":     targets["return_time"],
    }
    _DATE_FIELDS = frozenset({"pickup_date", "return_date"})

    for fname in ("pickup_location", "pickup_date", "return_date",
                  "pickup_time", "return_time"):
        rf = recipe.form_fields.get(fname)
        if rf is None:
            logger.log("recipe_field_missing", name=fname)
            continue

        # Range calendar auto-set — the pickup_date filler already set this
        if rf.strategy == "range_calendar_autofill":
            logger.log("recipe_field_skip", name=fname,
                       strategy="range_calendar_autofill")
            continue

        target_value = field_targets[fname]
        ifield = _make_identified_field(rf)
        logger.log("recipe_field_fill_start", name=fname, widget_type=rf.widget_type)

        if fname in _DATE_FIELDS:
            try:
                dw = _make_date_widget_info(rf)
                date_filler = get_date_filler(dw)
            except UnsupportedDateWidgetError as exc:
                raise RuntimeError(f"Unsupported date widget for {fname}: {exc}") from exc

            result = await date_filler.fill(session, ifield, dw, target_value, logger)
            logger.log("recipe_field_fill_done", name=fname, success=result.success,
                       error=result.error)
            if not result.success:
                raise RuntimeError(f"Date fill failed for {fname}: {result.error}")

        else:
            try:
                widget = _make_widget_info(rf)
                match_mode = rf.match_mode or "fuzzy"
                filler = get_filler_for_widget(widget, match_mode=match_mode)
            except UnsupportedWidgetError as exc:
                raise RuntimeError(f"Unsupported widget for {fname}: {exc}") from exc

            result = await filler.fill(
                session, ifield, widget, target_value, form_selector, logger
            )
            logger.log("recipe_field_fill_done", name=fname, success=result.success,
                       error=result.error)
            if not result.success:
                raise RuntimeError(f"Fill failed for {fname}: {result.error}")

        await _dismiss(session)


# ── Public entry point ────────────────────────────────────────────────────────

async def run_recipe(
    recipe: Recipe,
    targets: dict,
    log_dir: Path,
    headless: bool = True,
) -> ScrapeResult:
    """
    Execute the full scrape flow using a pre-built Recipe.  Zero LLM calls.

    Returns a ScrapeResult with:
      vehicles = DOM-extracted list (same field set as the LLM path)
      dom_vehicles = [] (no second path in recipe mode)
      llm_calls = 0
      cost_estimate_eur = 0.0
    """
    t0 = time.monotonic()
    scroll_rounds = 0
    scroll_final_count = 0
    has_empty_page = False
    vehicles: list[VehicleResult] = []

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
            mode="recipe",
            success=success,
            failed_phase=failed_phase,
            llm_calls=0,
            duration_s=round(_elapsed(), 2),
            vehicles=len(vehicles),
            scroll_rounds=scroll_rounds,
            scroll_final_count=scroll_final_count,
        )
        return ScrapeResult(
            url=recipe.url,
            targets=targets,
            success=success,
            failed_phase=failed_phase,
            error=error,
            vehicles=vehicles,
            dom_vehicles=[],
            form_fields=None,
            results_structure=None,
            verification=None,
            duration_seconds=_elapsed(),
            cost_estimate_eur=0.0,
            llm_calls=0,
            scroll_rounds=scroll_rounds,
            scroll_final_count=scroll_final_count,
            has_empty_page=has_empty_page,
        )

    try:
        async with BrowserSession(headless=headless) as session:

            # ── 1. Navigate ───────────────────────────────────────────────────
            logger.log("navigate", url=recipe.url)
            await session.navigate(recipe.url)
            logger.log("navigate_complete", url=recipe.url)

            # ── 2. Close cookies (heuristic, no LLM) ─────────────────────────
            try:
                cookie_result = await close_cookies_in_session(
                    session, log_dir, heuristic_only=True
                )
                logger.log("cookie_closer_invoked",
                           success=cookie_result.success, llm_calls=0)
            except Exception as exc:
                logger.log("cookie_closer_error", error=str(exc))

            # ── 3. Fill form from recipe (no LLM classification) ─────────────
            try:
                form_selector = await _detect_form_selector(session)
                await _fill_recipe_fields(
                    session, recipe, targets, form_selector, logger
                )
            except Exception as exc:
                logger.log("scrape_phase_error", phase="form", error=str(exc))
                return _make_result("form", f"Form fill exception: {exc}")

            # ── 4. Submit ─────────────────────────────────────────────────────
            url_before = session.get_url()
            try:
                clicked = await session.click_selector(
                    recipe.submit_selector,
                    recipe.submit_selector_type,
                )
                logger.log("submit_clicked",
                           selector=recipe.submit_selector, success=clicked)
                if not clicked:
                    return _make_result(
                        "submit",
                        f"Could not click submit: {recipe.submit_selector!r}",
                    )
            except Exception as exc:
                logger.log("scrape_phase_error", phase="submit", error=str(exc))
                return _make_result("submit", f"Submit exception: {exc}")

            # ── 5. Wait for results ───────────────────────────────────────────
            try:
                wait_outcome = await wait_for_results(session, url_before, logger)
                logger.log(
                    "results_waited",
                    ready=wait_outcome.ready,
                    signal=wait_outcome.signal,
                    waited_ms=wait_outcome.waited_ms,
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
                logger.log("scroll_summary",
                           rounds=scroll_rounds, final_count=scroll_final_count)
            except Exception as exc:
                logger.log("scroll_error", error=str(exc))

            # ── 6. Extract — DOM only, no LLM ────────────────────────────────
            try:
                structure = _recipe_to_results_structure(recipe)
                vehicles = await extract_vehicles_dom(session, structure, logger)
                logger.log("extraction_complete", count=len(vehicles))
            except Exception as exc:
                logger.log("extraction_error", error=str(exc))
                return _make_result("extraction", f"DOM extraction failed: {exc}")

            if not vehicles:
                if has_empty_page:
                    return _make_result(None, None, success=True)
                return _make_result("extraction",
                                    "DOM extraction returned no vehicles")

            return _make_result(None, None, success=True)

    except Exception as exc:
        logger.log("scrape_unexpected_error", error=str(exc))
        return _make_result(None, f"Unexpected error: {exc}")
