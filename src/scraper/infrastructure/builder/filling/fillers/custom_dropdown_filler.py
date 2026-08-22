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
from dataclasses import dataclass

from rapidfuzz import process, fuzz

from ...browser_session import BrowserSession
from ...field_analysis.field_identifier import IdentifiedField
from ...field_analysis.widget_classifier import WidgetInfo
from ...field_analysis.widget_opener import open_widget_reliably
from ..base_filler import FillResult, LocationFiller
from ...session_logger import SessionLogger

_FUZZY_THRESHOLD = 70
_MAX_SCROLL_STEPS = 20

_FALLBACK_CONTAINER = "[role='listbox']"
_FALLBACK_ITEM = "[role='option']"

# Text longer than this in a single "option" → looks like a concatenated blob
_BLOB_TEXT_THRESHOLD = 40
# A real option set has at least this many items
_MIN_PLAUSIBLE_OPTIONS = 3


# ── Option handles ────────────────────────────────────────────────────────────

@dataclass
class OptionHandle:
    """Encapsulates both the display text and how to click an option."""
    text: str
    click_base_selector: str   # CSS selector matching all options in the list
    click_index: int           # 0-based index within click_base_selector


# ── Heuristics ────────────────────────────────────────────────────────────────

def _looks_like_concatenated_blob(texts: list[str]) -> bool:
    """
    True if the read captured the container instead of individual items:
    very few elements where at least one has suspiciously long text.
    """
    if len(texts) > 2:
        return False
    return any(len(t) > _BLOB_TEXT_THRESHOLD for t in texts)


def _is_plausible_option_set(texts: list[str]) -> bool:
    """
    True if texts look like a real option set:
      - >= 3 non-empty elements
      - average text length < 40 chars
    """
    non_empty = [t for t in texts if t.strip()]
    if len(non_empty) < _MIN_PLAUSIBLE_OPTIONS:
        return False
    avg_len = sum(len(t) for t in non_empty) / len(non_empty)
    return avg_len < _BLOB_TEXT_THRESHOLD


# ── Robust option reader ──────────────────────────────────────────────────────

async def _read_options_robust(
    session: BrowserSession,
    options_container_selector: str,
    option_item_selector: str | None,
    logger: SessionLogger,
) -> tuple[list[OptionHandle], str]:
    """
    Read dropdown options resiliently. Returns (handles, strategy_name).

    Tries up to five strategies, stopping at the first that yields a
    plausible option set (>= 3 items with short, homogeneous texts).
    Detects the "concatenated blob" symptom (few elements, very long text)
    and falls through to the next strategy.

    Strategies tried in order:
      1. option_item_selector as-is (what the LLM gave)
      2. option_item_selector + " > *"  — direct children of the wrapper
      3. container + " [role='option']" — ARIA standard
      4. container + " [id*='option']"  — react-select pattern
      5. container + " [class*='option']" — class-name heuristic

    All strategies are generic; no provider-specific selectors.
    """
    container = options_container_selector

    strategies: list[tuple[str, str]] = []
    if option_item_selector:
        strategies.append(("item_selector", option_item_selector))
        strategies.append(("item_selector_children", option_item_selector + " > *"))

    strategies.extend([
        ("role_option",  f"{container} [role='option']"),
        ("id_option",    f"{container} [id*='option']"),
        ("class_option", f"{container} [class*='option']"),
    ])

    for strategy_name, selector in strategies:
        raw = await session.get_all_texts(selector)
        texts = [t.strip() for t in raw if t.strip()]

        if _looks_like_concatenated_blob(texts):
            logger.log("options_read_rejected", strategy=strategy_name,
                       reason="concatenated_blob", count=len(texts),
                       sample_len=len(texts[0]) if texts else 0)
            continue

        if _is_plausible_option_set(texts):
            logger.log("options_read_accepted", strategy=strategy_name,
                       count=len(texts), selector=selector)
            handles = [
                OptionHandle(text=t, click_base_selector=selector, click_index=i)
                for i, t in enumerate(texts)
            ]
            return handles, strategy_name

        logger.log("options_read_rejected", strategy=strategy_name,
                   reason="not_plausible", count=len(texts))

    logger.log("options_read_all_strategies_failed",
               container=container, item_selector=option_item_selector)
    return [], "failed"


# ── Matching ──────────────────────────────────────────────────────────────────

def _normalize_for_exact(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _match_option(
    target: str,
    handles: list[OptionHandle],
    mode: str,
) -> OptionHandle | None:
    """Return the matching OptionHandle or None."""
    texts = [h.text for h in handles]

    if mode == "exact":
        from ..time_utils import normalize_time

        norm_target = _normalize_for_exact(target)
        for h in handles:
            if _normalize_for_exact(h.text) == norm_target:
                return h

        t = normalize_time(target)
        if t is not None:
            for h in handles:
                if normalize_time(h.text) == t:
                    return h
        return None

    # fuzzy
    best = process.extractOne(
        target,
        texts,
        scorer=fuzz.WRatio,
        score_cutoff=_FUZZY_THRESHOLD,
    )
    if not best:
        return None
    matched_text, score, idx = best
    return handles[idx]


# ── Filler ────────────────────────────────────────────────────────────────────

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
        field: IdentifiedField | None = None,
    ) -> tuple[bool, str | None, str | None]:
        """
        Read options (robustly), match, and click.
        Scrolls the listbox up to _MAX_SCROLL_STEPS times for virtualized lists.

        Returns (success, matched_text, error).
        """
        container = widget.options_container_selector or _FALLBACK_CONTAINER
        item_sel = widget.option_item_selector  # may be None or wrong-level

        prev_texts: list[str] = []

        for step in range(_MAX_SCROLL_STEPS + 1):
            handles, strategy = await _read_options_robust(
                session, container, item_sel, logger
            )

            logger.log(
                "dropdown_options_read",
                step=step,
                count=len(handles),
                strategy=strategy,
            )

            if handles:
                match = _match_option(target_value, handles, self.match_mode)

                if match is not None:
                    logger.log(
                        "dropdown_match",
                        matched=match.text,
                        idx=match.click_index,
                        step=step,
                        match_mode=self.match_mode,
                        strategy=strategy,
                    )

                    clicked = await session.click_nth(
                        match.click_base_selector, match.click_index
                    )
                    if not clicked:
                        clicked = await session.click_option_by_text(
                            container, match.click_base_selector, match.text
                        )
                    if not clicked and field is not None:
                        # Toggle widgets: the classification pass opens the
                        # dropdown and the fill pass's click closes it again,
                        # leaving options in the DOM but invisible. Re-click
                        # the field to reopen, then retry the option.
                        logger.log("dropdown_retoggle_retry",
                                   selector=field.selector)
                        await session.click_selector(
                            field.selector, field.selector_type
                        )
                        await session.wait_ms(500)
                        clicked = await session.click_nth(
                            match.click_base_selector, match.click_index
                        )
                        if not clicked:
                            clicked = await session.click_option_by_text(
                                container, match.click_base_selector, match.text
                            )
                    if not clicked:
                        return (
                            False, None,
                            f"Could not click option {match.text!r} (idx={match.click_index})"
                        )
                    return True, match.text, None

                # No match — check end-of-list before scrolling
                cur_texts = [h.text for h in handles]
                if step > 0 and cur_texts == prev_texts:
                    logger.log("dropdown_end_of_list", step=step,
                               total_options=len(handles))
                    sample = cur_texts[:5]
                    return False, None, (
                        f"No {self.match_mode} match for {target_value!r} after scrolling "
                        f"{step} steps. Total options seen: {len(handles)}. Sample: {sample}"
                    )
                prev_texts = cur_texts
            else:
                # All strategies failed to read options
                if step > 0:
                    return False, None, (
                        f"Could not read any options from container {container!r} "
                        f"(tried {step + 1} scroll steps)"
                    )

            if step < _MAX_SCROLL_STEPS:
                logger.log("dropdown_scroll", step=step, delta_y=200)
                await session.scroll_container(container, delta_y=200)
                await session.wait_ms(300)

        sample = prev_texts[:5]
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

        # Step 2: find and click option (with robust reading + scroll support)
        success, matched_text, error = await self._find_and_click_option(
            session, widget, target_value, logger, field=field
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
