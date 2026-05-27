"""Scroll-to-complete safeguard: ensure the full vehicle list is rendered before extraction."""
from __future__ import annotations

import asyncio
import re

from ..browser_session import BrowserSession
from ..session_logger import SessionLogger

_SCROLL_SETTLE_S = 1.5          # seconds to wait after each scroll + load-more trigger
_STABLE_ROUNDS_NEEDED = 2       # consecutive stable card counts → list complete
_DEFAULT_MAX_ROUNDS = 8         # hard cap; keeps total overhead ≤ 12 s when stable at 2

# Multilingual "load more" text patterns (case-insensitive substring match)
_LOAD_MORE_PATTERNS = [
    "ver más", "cargar más", "mostrar más",
    "load more", "show more", "see more", "view more",
    "voir plus", "mehr laden", "carica altri",
]

# Count VEHICLE CARDS, not price occurrences.
# Mirrors the heuristic fallback in _MARK_CARDS_JS (results_extractor_dom):
#   walk up from visible price text nodes → nearest ancestor with heading + price
#   → apply leaf filter to deduplicate nested matches.
# Returns the number of distinct leaf cards visible on the page.
_CARD_COUNT_JS = """
() => {
    const priceRe = /[\\u20ac$\\u00a3]\\s*\\d+[\\d.,]*|\\d[\\d.,]*\\s*[\\u20ac$\\u00a3]|\\d[\\d.,]*\\s*(EUR|USD|GBP)/i;

    function hasPrice(el) {
        if (priceRe.test(el.textContent)) return true;
        for (const d of el.querySelectorAll('[aria-label]')) {
            if (priceRe.test(d.getAttribute('aria-label') || '')) return true;
        }
        return false;
    }

    // Walk up from each visible price text node to the nearest ancestor
    // that also contains a heading element — that element is a vehicle card.
    const visited = new Set();
    const candidates = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
        if (!priceRe.test(node.textContent)) continue;
        if (!node.parentElement || node.parentElement.offsetParent === null) continue;
        let el = node.parentElement;
        while (el && el !== document.body) {
            if (visited.has(el)) break;
            if (el.querySelector('h1,h2,h3,h4,h5,[role="heading"]') !== null && hasPrice(el)) {
                visited.add(el);
                candidates.push(el);
                break;
            }
            el = el.parentElement;
        }
    }
    // Leaf filter: discard any candidate that contains another candidate
    return candidates.filter(el =>
        !candidates.some(other => other !== el && el.contains(other))
    ).length;
}
"""


async def _count_vehicle_cards(session: BrowserSession) -> int:
    """Count visible vehicle cards (heading + price, leaf filter). Returns 0 on error."""
    try:
        return await session.page.evaluate(_CARD_COUNT_JS)
    except Exception:
        return 0


async def _scroll_page(session: BrowserSession) -> None:
    """Soft scroll to page bottom to trigger any lazy-loading. Non-destructive."""
    try:
        await session.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
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
    max_scroll_rounds: int = _DEFAULT_MAX_ROUNDS,
) -> tuple[int, int]:
    """
    Scroll progressively until the vehicle-card count stabilizes.

    Card counting uses the same heuristic as _mark_valid_cards (leaf element
    with heading + price) — one shared definition of "vehicle card". This is
    immune to each card having multiple price values (struck + final + aria-label)
    that would inflate a raw "€ occurrence" count.

    Each round:
      1. Count visible vehicle cards.
      2. If count unchanged for _STABLE_ROUNDS_NEEDED consecutive rounds, stop.
      3. Soft scroll to page bottom (single window.scrollTo, no keyboard events).
      4. Click a visible "load more / ver más / …" button if one is found.
      5. Wait _SCROLL_SETTLE_S seconds for newly triggered content to render.

    Stops early on stability or at max_scroll_rounds (hard cap).
    Provider-agnostic: no provider-specific selectors or class names.

    Returns (final_card_count, rounds_used).
    """
    stable_streak = 0
    prev_count = -1
    counts: list[int] = []

    for round_n in range(max_scroll_rounds):
        current = await _count_vehicle_cards(session)
        counts.append(current)

        if current == prev_count:
            stable_streak += 1
        else:
            stable_streak = 0
        prev_count = current

        logger.log(
            "scroll_round",
            round=round_n,
            card_count=current,
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

        await _scroll_page(session)
        clicked_more = await _try_click_load_more(session)
        if clicked_more:
            logger.log("scroll_load_more_clicked", round=round_n)

        await asyncio.sleep(_SCROLL_SETTLE_S)

    # Hit the hard cap
    final = await _count_vehicle_cards(session)
    logger.log(
        "scroll_complete",
        reason="max_rounds",
        rounds=max_scroll_rounds,
        final_count=final,
        counts=counts,
    )
    return final, max_scroll_rounds
