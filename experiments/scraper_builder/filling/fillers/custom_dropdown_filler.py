"""Filler for non-searchable custom dropdown widgets (is_searchable=False).

Strategy: open widget → read all visible options → match → click, scrolling the
listbox if the target option is not yet rendered (handles virtualized lists).

match_mode controls matching:
  "exact" (default) — normalized exact + time-aware fallback.
  "fuzzy"           — rapidfuzz WRatio threshold 70.
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
_MAX_SCROLL_STEPS = 20

_FALLBACK_CONTAINER = "[role='listbox']"
_FALLBACK_ITEM = "[role='option']"


def _normalize_for_exact(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _match_option(
    target: str,
    options: list[str],
    mode: str,
) -> tuple[str, int, float] | None:
    """
    Return (matched_text, index, score) or None.

    "exact" mode: normalized string equality, then time-aware fallback.
    "fuzzy" mode: rapidfuzz WRatio with threshold 70.
    """
    if mode == "exact":
        from filling.time_utils import normalize_time

        norm_target = _normalize_for_exact(target)
        for idx, opt in enumerate(options):
            if _normalize_for_exact(opt) == norm_target:
                return opt, idx, 100.0

        t = normalize_time(target)
        if t is not None:
            for idx, opt in enumerate(options):
                if normalize_time(opt) == t:
                    return opt, idx, 100.0

        return None

    # fuzzy
    best = process.extractOne(
        target,
        options,
        scorer=fuzz.WRatio,
        score_cutoff=_FUZZY_THRESHOLD,
    )
    if not best:
        return None
    matched_text, score, idx = best
    return matched_text, idx, float(score)


class CustomDropdownFiller(LocationFiller):
    """
    Provider-agnostic filler for non-searchable dropdown widgets.
    All selectors come from IdentifiedField and WidgetInfo — nothing hardcoded.
    """

    strategy_name = "custom_dropdown"

    def __init__(self, match_mode: str = "exact") -> None:
        self.match_mode = match_mode

    async def _find_and_click_option(
        self,
        session: BrowserSession,
        widget: WidgetInfo,
        target_value: str,
        logger: SessionLogger,
    ) -> tuple[bool, str | None, str | None]:
        """
        Read options, match, and click. Scrolls the listbox up to
        _MAX_SCROLL_STEPS times if the option is not yet rendered.

        Returns (success, matched_text, error).
        """
        container = widget.options_container_selector or _FALLBACK_CONTAINER
        item_sel = widget.option_item_selector or _FALLBACK_ITEM

        prev_options: list[str] = []

        for step in range(_MAX_SCROLL_STEPS + 1):
            options_texts: list[str] = await session.get_all_texts(item_sel)
            options_texts = [t.strip() for t in options_texts if t.strip()]

            logger.log(
                "dropdown_options_read",
                step=step,
                count=len(options_texts),
                container=container,
                item_selector=item_sel,
            )

            result = _match_option(target_value, options_texts, self.match_mode)

            if result is not None:
                matched_text, idx, score = result
                logger.log(
                    "dropdown_match",
                    matched=matched_text,
                    score=round(score, 1),
                    idx=idx,
                    step=step,
                    match_mode=self.match_mode,
                )

                clicked = await session.click_nth(item_sel, idx)
                if not clicked:
                    clicked = await session.click_option_by_text(
                        container, item_sel, matched_text
                    )
                if not clicked:
                    return False, None, f"Could not click option {matched_text!r} (idx={idx})"
                return True, matched_text, None

            # No match yet — check if the list has grown (i.e. new options rendered)
            if step > 0 and options_texts == prev_options:
                logger.log("dropdown_end_of_list", step=step, total_options=len(options_texts))
                sample = options_texts[:5]
                return False, None, (
                    f"No {self.match_mode} match for {target_value!r} after scrolling "
                    f"{step} steps. Total options seen: {len(options_texts)}. "
                    f"Sample: {sample}"
                )

            prev_options = list(options_texts)

            if step < _MAX_SCROLL_STEPS:
                logger.log("dropdown_scroll", step=step, delta_y=200)
                await session.scroll_container(container, delta_y=200)
                await session.wait_ms(300)

        sample = prev_options[:5]
        return False, None, (
            f"No {self.match_mode} match for {target_value!r} after {_MAX_SCROLL_STEPS} "
            f"scroll steps. Sample: {sample}"
        )

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

        logger.log(
            "filler_started",
            strategy=self.strategy_name,
            target=target_value,
            match_mode=self.match_mode,
        )

        # Step 1: open widget reliably
        open_result = await open_widget_reliably(
            session, field, logger, label="custom_dropdown"
        )
        if not open_result.opened:
            return _fail(f"Could not open dropdown: {open_result.error}")

        logger.log(
            "dropdown_opened",
            method=open_result.method,
            options=open_result.options_detected,
        )

        # Snapshot open listbox HTML
        container = widget.options_container_selector or _FALLBACK_CONTAINER
        listbox_html = await session.get_inner_html(container)
        log_dir = logger.log_dir
        (log_dir / "dom_snapshots" / "07_custom_dropdown_open.html").write_text(
            listbox_html, encoding="utf-8"
        )

        # Step 2: find and click option (with scroll support)
        success, matched_text, error = await self._find_and_click_option(
            session, widget, target_value, logger
        )

        if not success:
            return _fail(error or "Option not found")

        await session.wait_ms(500)
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
