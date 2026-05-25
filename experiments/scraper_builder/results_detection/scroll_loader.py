"""Scroll-to-complete safeguard: ensure the full vehicle list is rendered before extraction."""
from __future__ import annotations

import asyncio
import re

from browser_session import BrowserSession
from session_logger import SessionLogger

_SCROLL_SETTLE_S = 1.5          # seconds to wait after each scroll + load-more trigger
_STABLE_ROUNDS_NEEDED = 2       # consecutive rounds with same count → list complete

# Multilingual "load more" text patterns (case-insensitive substring match)
_LOAD_MORE_PATTERNS = [
    "ver más", "cargar más", "mostrar más",
    "load more", "show more", "see more", "view more",
    "voir plus", "mehr laden", "carica altri",
]

# Same currency-pattern TreeWalker used by results_waiter — no class-name dependency.
_PRICE_COUNT_JS = """
() => {
    const priceRe = /[\\u20ac$\\u00a3]\\s*\\d+([.,]\\d{1,2})?|\\d+([.,]\\d{1,2})?\\s*[\\u20ac$\\u00a3]|\\d+([.,]\\d{1,2})?\\s*(EUR|USD|GBP)/i;
    const seen = new Set();
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
        if (priceRe.test(node.textContent)) {
            const parent = node.parentElement;
            if (parent && parent.offsetParent !== null) seen.add(parent);
        }
    }
    return seen.size;
}
"""

# Scroll both the window and any overflow-scroll containers to their bottom.
_SCROLL_ALL_JS = """
() => {
    window.scrollTo(0, document.body.scrollHeight);
    let scrolled = 0;
    for (const el of document.querySelectorAll('*')) {
        const s = window.getComputedStyle(el);
        if ((s.overflowY === 'scroll' || s.overflowY === 'auto') &&
                el.scrollHeight > el.clientHeight + 50) {
            el.scrollTop = el.scrollHeight;
            scrolled++;
        }
    }
    return scrolled;
}
"""


async def _count_price_elements(session: BrowserSession) -> int:
    try:
        return await session.page.evaluate(_PRICE_COUNT_JS)
    except Exception:
        return 0


async def _scroll_all(session: BrowserSession) -> None:
    """Scroll the page and any overflow containers to the bottom, press End for reinforcement."""
    try:
        await session.page.evaluate(_SCROLL_ALL_JS)
        await session.page.keyboard.press("End")
    except Exception:
        pass


async def _try_click_load_more(session: BrowserSession) -> bool:
    """Find and click the first visible 'load more / ver más / …' button.

    Returns True if a button was found and clicked.
    """
    for pattern in _LOAD_MORE_PATTERNS:
        try:
            loc = (
                session.page
                .locator('button, a, [role="button"]')
                .filter(has_text=re.compile(pattern, re.IGNORECASE))
                .first
            )
            if await loc.is_visible():
                await loc.click(timeout=2_000)
                return True
        except Exception:
            continue
    return False


async def ensure_all_results_loaded(
    session: BrowserSession,
    logger: SessionLogger,
    max_scroll_rounds: int = 25,
) -> tuple[int, int]:
    """
    Scroll progressively until the count of price-containing elements stabilizes.

    Each round:
      1. Count visible price-containing elements.
      2. If unchanged for _STABLE_ROUNDS_NEEDED consecutive rounds, stop.
      3. Scroll to bottom (page + any overflow containers) and press End.
      4. Click a visible "load more / ver más / …" button if one exists.
      5. Wait _SCROLL_SETTLE_S seconds for newly triggered content to render.

    Stops early on stability or at max_scroll_rounds (hard cap).
    Provider-agnostic: no provider-specific selectors or text.

    Returns (final_count, rounds_used).
    """
    stable_streak = 0
    prev_count = -1
    counts: list[int] = []

    for round_n in range(max_scroll_rounds):
        current = await _count_price_elements(session)
        counts.append(current)

        if current == prev_count:
            stable_streak += 1
        else:
            stable_streak = 0
        prev_count = current

        logger.log(
            "scroll_round",
            round=round_n,
            count=current,
            stable_streak=stable_streak,
        )

        if stable_streak >= _STABLE_ROUNDS_NEEDED:
            logger.log(
                "scroll_complete",
                reason="stable",
                rounds=round_n + 1,
                final_count=current,
                counts=counts,
            )
            return current, round_n + 1

        await _scroll_all(session)
        clicked_more = await _try_click_load_more(session)
        if clicked_more:
            logger.log("scroll_load_more_clicked", round=round_n)

        await asyncio.sleep(_SCROLL_SETTLE_S)

    # Hit the hard cap
    final = await _count_price_elements(session)
    logger.log(
        "scroll_complete",
        reason="max_rounds",
        rounds=max_scroll_rounds,
        final_count=final,
        counts=counts,
    )
    return final, max_scroll_rounds
