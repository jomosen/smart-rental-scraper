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
from contextlib import asynccontextmanager
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


# ── Session context helper ────────────────────────────────────────────────────

@asynccontextmanager
async def _session_ctx(external: BrowserSession | None, headless: bool):
    """Yield *external* unchanged if provided; otherwise open a new BrowserSession."""
    if external is not None:
        yield external
    else:
        async with BrowserSession(headless=headless) as s:
            yield s


# ── Public entry points ───────────────────────────────────────────────────────

async def run_recipe(
    recipe: Recipe,
    targets: dict,
    log_dir: Path,
    headless: bool = True,
    session: BrowserSession | None = None,
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
        async with _session_ctx(session, headless) as session:

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


# ── Refine helpers (shared by _refine_navigate and _refine_in_place) ─────────

from dataclasses import dataclass as _dataclass


@_dataclass
class _ExtractOut:
    vehicles: list
    scroll_rounds: int
    scroll_final_count: int
    has_empty_page: bool
    failed_phase: str | None
    error: str | None


async def _fill_date_fields(
    session: BrowserSession,
    recipe: Recipe,
    pickup_date: date,
    return_date: date,
    logger: SessionLogger,
) -> None:
    """Fill pickup and return date fields explicitly. Never relies on autofill.

    For range-calendar providers (return_date.strategy='range_calendar_autofill'):
      - Fills pickup_date without dismissing so the calendar stays open.
      - Fills return_date reusing pickup_date's DateWidgetInfo (same calendar
        container/nav selectors) but return_date's own selector.
        DateCalendarFiller detects the calendar is already open and skips the
        open-click.  If the calendar closed after the pickup click, it is
        reopened via return_date's own selector.
    Raises RuntimeError on any fill failure.
    """
    pickup_rf = recipe.form_fields.get("pickup_date")
    return_rf = recipe.form_fields.get("return_date")
    is_range = return_rf is not None and return_rf.strategy == "range_calendar_autofill"

    # ── pickup_date ──────────────────────────────────────────────────────────
    if pickup_rf is None:
        logger.log("refine_dates_field_missing", name="pickup_date")
    else:
        dw = _make_date_widget_info(pickup_rf)
        try:
            filler = get_date_filler(dw)
        except UnsupportedDateWidgetError as exc:
            raise RuntimeError(f"Unsupported date widget for pickup_date: {exc}") from exc
        result = await filler.fill(
            session, _make_identified_field(pickup_rf), dw, pickup_date, logger
        )
        logger.log("refine_pickup_set", date=pickup_date.isoformat(),
                   success=result.success, error=result.error)
        if not result.success:
            raise RuntimeError(f"Date fill failed for pickup_date: {result.error}")
        # Don't dismiss range calendars — the calendar stays open for return_date.
        if not is_range:
            await _dismiss(session)

    # ── return_date — always explicit, never depends on autofill ─────────────
    if return_rf is None:
        logger.log("refine_dates_field_missing", name="return_date")
    else:
        # Range calendars: use pickup_date's DateWidgetInfo so the filler can
        # detect the already-open calendar and reuse it (skipping the open-click).
        # return_date's own selector is passed as the field so DateCalendarFiller
        # can reopen the calendar via that selector if it closed after pickup.
        if is_range and pickup_rf is not None:
            calendar_dw = _make_date_widget_info(pickup_rf)
        else:
            calendar_dw = _make_date_widget_info(return_rf)
        try:
            filler = get_date_filler(calendar_dw)
        except UnsupportedDateWidgetError as exc:
            raise RuntimeError(f"Unsupported date widget for return_date: {exc}") from exc
        result = await filler.fill(
            session, _make_identified_field(return_rf), calendar_dw, return_date, logger
        )
        logger.log("refine_return_set", date=return_date.isoformat(),
                   success=result.success, error=result.error)
        if not result.success:
            raise RuntimeError(f"Date fill failed for return_date: {result.error}")
        await _dismiss(session)


async def _submit_wait_extract(
    session: BrowserSession,
    recipe: Recipe,
    logger: SessionLogger,
) -> _ExtractOut:
    """Click submit, wait for results, scroll, extract.  Returns _ExtractOut.
    failed_phase is None on full success."""
    vehicles: list[VehicleResult] = []
    scroll_rounds = 0
    scroll_final_count = 0
    has_empty_page = False

    url_before = session.get_url()
    try:
        clicked = await session.click_selector(
            recipe.submit_selector, recipe.submit_selector_type
        )
        logger.log("submit_clicked", selector=recipe.submit_selector, success=clicked)
        if not clicked:
            return _ExtractOut(vehicles, 0, 0, False, "submit",
                               f"Could not click submit: {recipe.submit_selector!r}")
    except Exception as exc:
        logger.log("scrape_phase_error", phase="submit", error=str(exc))
        return _ExtractOut(vehicles, 0, 0, False, "submit", f"Submit exception: {exc}")

    try:
        wait_outcome = await wait_for_results(session, url_before, logger)
        logger.log("results_waited", ready=wait_outcome.ready,
                   signal=wait_outcome.signal, waited_ms=wait_outcome.waited_ms)
        has_empty_page = wait_outcome.signal == "empty_message"
        if not wait_outcome.ready:
            return _ExtractOut(vehicles, 0, 0, has_empty_page, "results",
                               f"Results page did not load (signal={wait_outcome.signal})")
    except Exception as exc:
        logger.log("scrape_phase_error", phase="results", error=str(exc))
        return _ExtractOut(vehicles, 0, 0, False, "results", f"Results wait exception: {exc}")

    try:
        scroll_final_count, scroll_rounds = await ensure_all_results_loaded(session, logger)
        logger.log("scroll_summary", rounds=scroll_rounds, final_count=scroll_final_count)
    except Exception as exc:
        logger.log("scroll_error", error=str(exc))

    try:
        structure = _recipe_to_results_structure(recipe)
        vehicles = await extract_vehicles_dom(session, structure, logger)
        logger.log("extraction_complete", count=len(vehicles))
    except Exception as exc:
        logger.log("extraction_error", error=str(exc))
        return _ExtractOut(vehicles, scroll_rounds, scroll_final_count, has_empty_page,
                           "extraction", f"DOM extraction failed: {exc}")

    return _ExtractOut(vehicles, scroll_rounds, scroll_final_count, has_empty_page, None, None)


# ── Strategy implementations ──────────────────────────────────────────────────

async def _refine_navigate(
    session: BrowserSession,
    recipe: Recipe,
    pickup_date: date,
    return_date: date,
    log_dir: Path,
) -> ScrapeResult:
    """Navigate to recipe.refine_url, fill dates only, submit and extract.

    Cookies are NOT re-closed — the persistent session already accepted them.
    failed_phase "refine_navigate" covers both the navigation and date-fill steps.
    """
    t0 = time.monotonic()
    logger = SessionLogger(log_dir)
    vehicles: list[VehicleResult] = []
    scroll_rounds = 0
    scroll_final_count = 0
    has_empty_page = False

    def _elapsed() -> float:
        return time.monotonic() - t0

    def _make_result(fp: str | None, err: str | None, success: bool = False) -> ScrapeResult:
        logger.log("scrape_complete", mode="refine_navigate", success=success,
                   failed_phase=fp, llm_calls=0, duration_s=round(_elapsed(), 2),
                   vehicles=len(vehicles), scroll_rounds=scroll_rounds,
                   scroll_final_count=scroll_final_count)
        return ScrapeResult(
            url=session.get_url(),
            targets={"pickup_date": pickup_date, "return_date": return_date},
            success=success, failed_phase=fp, error=err, vehicles=vehicles,
            dom_vehicles=[], form_fields=None, results_structure=None,
            verification=None, duration_seconds=_elapsed(), cost_estimate_eur=0.0,
            llm_calls=0, scroll_rounds=scroll_rounds,
            scroll_final_count=scroll_final_count, has_empty_page=has_empty_page,
        )

    if not recipe.refine_url:
        return _make_result(
            "refine_navigate",
            "refine_strategy='navigate_and_change_dates' requires refine_url to be set",
        )

    try:
        logger.log("refine_navigate", url=recipe.refine_url)
        await session.navigate(recipe.refine_url)
        pickup_rf = recipe.form_fields.get("pickup_date")
        if pickup_rf:
            await session.wait_for_selector(pickup_rf.selector)
        logger.log("refine_navigate_ready", url=recipe.refine_url)
    except Exception as exc:
        logger.log("scrape_phase_error", phase="refine_navigate", error=str(exc))
        return _make_result("refine_navigate", f"Navigate failed: {exc}")

    try:
        await _fill_date_fields(session, recipe, pickup_date, return_date, logger)
    except Exception as exc:
        logger.log("scrape_phase_error", phase="refine_navigate", error=str(exc))
        return _make_result("refine_navigate", f"Date fill exception: {exc}")

    out = await _submit_wait_extract(session, recipe, logger)
    vehicles = out.vehicles
    scroll_rounds = out.scroll_rounds
    scroll_final_count = out.scroll_final_count
    has_empty_page = out.has_empty_page
    if out.failed_phase:
        return _make_result(out.failed_phase, out.error)
    if not vehicles:
        if has_empty_page:
            return _make_result(None, None, success=True)
        return _make_result("extraction", "DOM extraction returned no vehicles")
    return _make_result(None, None, success=True)


async def _refine_in_place(
    session: BrowserSession,
    recipe: Recipe,
    pickup_date: date,
    return_date: date,
    log_dir: Path,
) -> ScrapeResult:
    """Change dates on the current results page without navigating away.

    Not implemented: no active recipe uses this strategy yet.  Will be
    implemented when a provider is confirmed to keep the form calendar
    accessible on its results page.
    """
    raise NotImplementedError(
        "refine_strategy='in_place' is not yet implemented. "
        "Use 'navigate_and_change_dates' (with refine_url) or 'none'."
    )


# ── Public dispatcher ─────────────────────────────────────────────────────────

async def refine_dates(
    session: BrowserSession,
    recipe: Recipe,
    pickup_date: date,
    return_date: date,
    log_dir: Path,
) -> ScrapeResult:
    """Dispatch to the per-recipe refine strategy.

    strategy='navigate_and_change_dates' — navigate to recipe.refine_url,
        fill dates, submit, extract.  Fastest path when the provider exposes
        a deep-link that re-fills form fields from cookies.
    strategy='in_place' — fill dates on the current results page (skeleton;
        raises NotImplementedError until a provider is confirmed to need it).
    strategy='none' or missing — raises NotImplementedError; caller must
        fall back to a full _submit_form.
    """
    strategy = (recipe.refine_strategy or "none").lower()
    if strategy == "navigate_and_change_dates":
        return await _refine_navigate(session, recipe, pickup_date, return_date, log_dir)
    if strategy == "in_place":
        return await _refine_in_place(session, recipe, pickup_date, return_date, log_dir)
    raise NotImplementedError(
        f"refine_strategy={strategy!r} — caller must fall back to _submit_form"
    )
