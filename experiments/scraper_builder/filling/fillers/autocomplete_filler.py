"""Filler for widgets classified as autocomplete + is_searchable=True.

Strategy: click to focus → type target → wait for options → fuzzy match → click option.
Works with any widget that exposes options via the DOM after typing (react-select,
MUI Autocomplete, Ant Design AutoComplete, custom hand-rolled dropdowns, etc.).
"""
from __future__ import annotations

import time

from rapidfuzz import process, fuzz

from browser_session import BrowserSession
from field_analysis.field_identifier import IdentifiedField
from field_analysis.widget_classifier import WidgetInfo
from filling.base_filler import FillResult, LocationFiller
from session_logger import SessionLogger

_FUZZY_THRESHOLD = 70

# ARIA-role fallbacks when the widget_classifier didn't identify specific selectors.
_FALLBACK_CONTAINER = "[role='listbox']"
_FALLBACK_ITEM = "[role='option']"


class AutocompleteFiller(LocationFiller):
    """
    Provider-agnostic filler for searchable autocomplete widgets.
    All selectors come from IdentifiedField and WidgetInfo — nothing hardcoded.
    """

    strategy_name = "autocomplete_searchable"

    async def fill(
        self,
        session: BrowserSession,
        field: IdentifiedField,
        widget: WidgetInfo,
        target_value: str,
        form_selector: str,
        logger: SessionLogger,
    ) -> FillResult:
        start = time.monotonic()
        log_dir = logger.log_dir

        def _fail(msg: str) -> FillResult:
            logger.log("filler_failed", error=msg, strategy=self.strategy_name)
            return FillResult(
                success=False,
                strategy_used=self.strategy_name,
                target_value=target_value,
                matched_option=None,
                state_changes=None,
                duration_seconds=time.monotonic() - start,
                error=msg,
            )

        logger.log("filler_started", strategy=self.strategy_name, target=target_value)

        # Step 1: click to open/focus dropdown
        clicked = await session.click_selector(field.selector, field.selector_type)
        if not clicked:
            return _fail(f"Could not click field selector: {field.selector!r}")
        await session.wait_ms(1_500)
        logger.log("filler_opened_dropdown", selector=field.selector)

        # Step 2: type target value (character-by-character to trigger onChange)
        await session.type_text(field.selector, target_value, field.selector_type)
        await session.wait_ms(1_500)
        logger.log("filler_typed", target_value=target_value)

        # Step 3: read options from the DOM
        container = widget.options_container_selector or _FALLBACK_CONTAINER
        item_sel = widget.option_item_selector or _FALLBACK_ITEM

        options_html = await session.get_inner_html(container)
        (log_dir / "dom_snapshots" / "06_options_after_typing.html").write_text(
            options_html, encoding="utf-8"
        )

        options_texts: list[str] = await session.get_all_texts(item_sel)
        options_texts = [t.strip() for t in options_texts if t.strip()]
        logger.log("filler_options_captured",
                   count=len(options_texts),
                   container=container,
                   item_selector=item_sel)

        if not options_texts:
            return _fail(
                f"No options visible after typing {target_value!r} "
                f"(container={container!r}, item={item_sel!r})"
            )

        # Step 4: fuzzy match
        best = process.extractOne(
            target_value,
            options_texts,
            scorer=fuzz.WRatio,
            score_cutoff=_FUZZY_THRESHOLD,
        )
        if not best:
            sample = options_texts[:5]
            return _fail(
                f"No fuzzy match (threshold={_FUZZY_THRESHOLD}) for {target_value!r} "
                f"in {len(options_texts)} options. Sample: {sample}"
            )

        matched_text, score, _ = best
        logger.log("filler_match", matched=matched_text, score=round(score, 1),
                   candidate_count=len(options_texts))

        # Step 5: click the matched option
        clicked_opt = await session.click_option_by_text(container, item_sel, matched_text)
        if not clicked_opt:
            return _fail(f"Could not click option {matched_text!r}")
        await session.wait_ms(1_000)
        logger.log("filler_clicked_option", text=matched_text)

        return FillResult(
            success=True,
            strategy_used=self.strategy_name,
            target_value=target_value,
            matched_option=matched_text,
            state_changes=None,
            duration_seconds=time.monotonic() - start,
            error=None,
        )
