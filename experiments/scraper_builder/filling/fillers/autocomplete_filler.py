"""Filler for widgets classified as autocomplete + is_searchable=True.

Strategy: click to focus -> type target -> wait for options -> match -> click option.
Works with any widget that exposes options via the DOM after typing (react-select,
MUI Autocomplete, Ant Design AutoComplete, custom hand-rolled dropdowns, etc.).

match_mode controls how the typed text is matched against the options list:
  "fuzzy" (default) — rapidfuzz WRatio with threshold 70; good for locations.
  "exact"           — normalized exact match, with time-aware fallback; good for
                      times and other discrete values where fuzzy would be too lax.
"""
from __future__ import annotations

import re
import time

from rapidfuzz import process, fuzz

from browser_session import BrowserSession
from field_analysis.field_identifier import IdentifiedField
from field_analysis.widget_classifier import WidgetInfo
from field_analysis.widget_opener import open_widget_reliably
from filling.base_filler import FillResult, LocationFiller
from session_logger import SessionLogger

_FUZZY_THRESHOLD = 70

# ARIA-role fallbacks when the widget_classifier didn't identify specific selectors.
_FALLBACK_CONTAINER = "[role='listbox']"
_FALLBACK_ITEM = "[role='option']"


def _normalize_for_exact(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


class AutocompleteFiller(LocationFiller):
    """
    Provider-agnostic filler for searchable autocomplete widgets.
    All selectors come from IdentifiedField and WidgetInfo — nothing hardcoded.
    """

    strategy_name = "autocomplete_searchable"

    def __init__(self, match_mode: str = "fuzzy") -> None:
        self.match_mode = match_mode

    def _match_exact(
        self, target: str, options: list[str]
    ) -> tuple[str, int] | None:
        """
        Return (matched_text, index) using normalized exact comparison.
        Falls back to time-normalized comparison when target looks like a time.
        """
        from filling.time_utils import normalize_time

        norm_target = _normalize_for_exact(target)
        for idx, opt in enumerate(options):
            if _normalize_for_exact(opt) == norm_target:
                return opt, idx

        # If target looks like a time, compare by (hour, minute) to tolerate
        # format differences like "10:00 AM" vs "10:00".
        t = normalize_time(target)
        if t is not None:
            for idx, opt in enumerate(options):
                if normalize_time(opt) == t:
                    return opt, idx

        return None

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

        logger.log("filler_started", strategy=self.strategy_name,
                   target=target_value, match_mode=self.match_mode)

        # Step 1: open dropdown reliably (polling-based, not a blind fixed wait)
        open_result = await open_widget_reliably(
            session, field, logger, label="autocomplete"
        )
        if not open_result.opened:
            return _fail(f"Could not open dropdown: {open_result.error}")
        logger.log("filler_opened_dropdown",
                   method=open_result.method, options=open_result.options_detected)

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

        # Step 4: match according to match_mode
        if self.match_mode == "exact":
            exact_result = self._match_exact(target_value, options_texts)
            if exact_result is None:
                sample = options_texts[:5]
                return _fail(
                    f"No exact match for {target_value!r} in "
                    f"{len(options_texts)} options. Sample: {sample}"
                )
            matched_text, idx = exact_result
            score = 100
        else:
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
            matched_text, score, idx = best

        logger.log("filler_match", matched=matched_text, score=round(score, 1),
                   idx=idx, candidate_count=len(options_texts),
                   match_mode=self.match_mode)

        # Step 5: click the matched option by index (same query as get_all_texts,
        # so index is guaranteed to correspond to the right element).
        clicked_opt = await session.click_nth(item_sel, idx)
        if not clicked_opt:
            # Fallback: text-based click in case index drifted
            clicked_opt = await session.click_option_by_text(container, item_sel, matched_text)
        if not clicked_opt:
            return _fail(f"Could not click option {matched_text!r} (idx={idx})")
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
