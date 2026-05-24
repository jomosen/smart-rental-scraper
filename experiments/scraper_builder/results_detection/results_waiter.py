"""Heuristic poller: wait until the results page is ready after form submit."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from browser_session import BrowserSession
from session_logger import SessionLogger

_POLL_INTERVAL_S = 0.7
_SETTLE_DELAY_S = 0.8
_PRICE_ELEMENT_THRESHOLD = 3
_STABLE_ITERATIONS_NEEDED = 2

# Multilingual "no results" keywords (lower-case)
_EMPTY_KEYWORDS = [
    "no results",
    "no se encontraron",
    "no hay resultados",
    "sin resultados",
    "no vehicles",
    "no cars available",
    "keine ergebnisse",
    "aucun résultat",
    "nessun risultato",
    "no availability",
    "not available",
    "no hay vehículos",
    "lo sentimos",
    "sorry, no",
]

# Class/ARIA fragments that indicate a loading spinner
_SPINNER_CLASS_FRAGMENTS = [
    "spinner",
    "loading",
    "loader",
    "skeleton",
    "cargando",
    "searching",
    "buscando",
]


@dataclass
class WaitOutcome:
    ready: bool
    signal: str          # "result_elements" | "empty_message" | "dom_stable" | "timeout"
    url_before: str
    url_after: str
    candidate_count: int
    spinner_gone: bool
    waited_ms: int


# ── JS helpers ────────────────────────────────────────────────────────────────

async def _count_price_like_elements(session: BrowserSession) -> int:
    """
    Count visible leaf-level elements whose text matches a currency price pattern.
    Uses a TreeWalker over text nodes — never depends on CSS class names.
    """
    try:
        count: int = await session.page.evaluate("""
            () => {
                const priceRe = /[\\u20ac$\\u00a3]\\s*\\d+([.,]\\d{1,2})?|\\d+([.,]\\d{1,2})?\\s*[\\u20ac$\\u00a3]|\\d+([.,]\\d{1,2})?\\s*(EUR|USD|GBP)/i;
                const seen = new Set();
                const walker = document.createTreeWalker(
                    document.body,
                    NodeFilter.SHOW_TEXT
                );
                let node;
                while ((node = walker.nextNode())) {
                    if (priceRe.test(node.textContent)) {
                        const parent = node.parentElement;
                        if (parent && parent.offsetParent !== null) {
                            seen.add(parent);
                        }
                    }
                }
                return seen.size;
            }
        """)
        return count
    except Exception:
        return 0


async def _has_visible_spinner(session: BrowserSession) -> bool:
    """Return True if any ARIA or CSS spinner is currently visible."""
    try:
        has_spinner: bool = await session.page.evaluate(
            """(fragments) => {
                const ariaEls = document.querySelectorAll(
                    '[role="progressbar"],[aria-busy="true"]'
                );
                for (const el of ariaEls) {
                    if (el.offsetParent !== null) return true;
                }
                for (const frag of fragments) {
                    const pattern = '[class*="' + frag + '"]';
                    const els = document.querySelectorAll(pattern);
                    for (const el of els) {
                        if (el.offsetParent !== null) return true;
                    }
                }
                return false;
            }""",
            _SPINNER_CLASS_FRAGMENTS,
        )
        return has_spinner
    except Exception:
        return False


async def _has_empty_results_message(session: BrowserSession) -> bool:
    """Return True if the page body contains a multilingual 'no results' keyword."""
    try:
        body_text: str = await session.page.evaluate(
            "() => document.body ? document.body.innerText.toLowerCase() : ''"
        )
        return any(kw in body_text for kw in _EMPTY_KEYWORDS)
    except Exception:
        return False


async def _get_dom_content_length(session: BrowserSession) -> int:
    """Return page text-content byte count as a proxy for DOM stability."""
    try:
        length: int = await session.page.evaluate(
            "() => document.body ? document.body.textContent.length : 0"
        )
        return length
    except Exception:
        return -1


# ── Main waiter ───────────────────────────────────────────────────────────────

async def wait_for_results(
    session: BrowserSession,
    url_before_submit: str,
    logger: SessionLogger,
    timeout_ms: int = 30_000,
) -> WaitOutcome:
    """
    Poll until the results page is ready or timeout.

    url_changed is a transition event, not a ready signal: when navigation
    is detected, polling continues on the new page looking for real content.

    Ready signals:
      "result_elements" — ≥3 visible elements whose text matches a price pattern
      "empty_message"   — a multilingual "no results" phrase detected in page body
      "dom_stable"      — DOM text size unchanged ≥2 consecutive polls + no spinner
                          (only checked after URL change; fallback for pages whose
                          prices our regex can't detect)

    When a signal fires and no spinner is visible, a _SETTLE_DELAY_S pause lets
    the render complete before the caller captures HTML.

    Returns ready=False with signal="timeout" after *timeout_ms* ms.
    """
    signal: str | None = None
    candidate_count = 0
    spinner_gone = True
    url_changed_at: int | None = None
    prev_dom_size = -1
    stable_count = 0
    poll_n = 0
    t0 = time.monotonic()

    while True:
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        if elapsed_ms >= timeout_ms:
            logger.log(
                "results_wait_timeout",
                waited_ms=elapsed_ms,
                last_signal=signal,
                candidate_count=candidate_count,
            )
            return WaitOutcome(
                ready=False,
                signal="timeout",
                url_before=url_before_submit,
                url_after=session.get_url(),
                candidate_count=candidate_count,
                spinner_gone=spinner_gone,
                waited_ms=elapsed_ms,
            )

        current_url = session.get_url()

        # Detect URL transition — log once, do NOT mark ready
        if url_changed_at is None and current_url != url_before_submit:
            url_changed_at = elapsed_ms
            prev_dom_size = -1
            stable_count = 0
            logger.log(
                "results_url_changed",
                url_after=current_url,
                elapsed_ms=elapsed_ms,
            )

        candidate_count = await _count_price_like_elements(session)
        has_empty = await _has_empty_results_message(session)
        spinner_gone = not await _has_visible_spinner(session)

        if candidate_count >= _PRICE_ELEMENT_THRESHOLD:
            signal = "result_elements"
        elif has_empty:
            signal = "empty_message"
        elif url_changed_at is not None:
            # DOM stability only meaningful after navigation
            dom_size = await _get_dom_content_length(session)
            if dom_size > 0 and dom_size == prev_dom_size:
                stable_count += 1
            else:
                stable_count = 0
            prev_dom_size = dom_size

            if stable_count >= _STABLE_ITERATIONS_NEEDED and spinner_gone:
                signal = "dom_stable"

        logger.log(
            "results_wait_poll",
            poll=poll_n,
            elapsed_ms=elapsed_ms,
            signal=signal,
            url_changed=(url_changed_at is not None),
            candidate_count=candidate_count,
            has_empty=has_empty,
            spinner_gone=spinner_gone,
            stable_count=stable_count,
        )

        if signal is not None and spinner_gone:
            await asyncio.sleep(_SETTLE_DELAY_S)
            candidate_count = await _count_price_like_elements(session)
            return WaitOutcome(
                ready=True,
                signal=signal,
                url_before=url_before_submit,
                url_after=session.get_url(),
                candidate_count=candidate_count,
                spinner_gone=True,
                waited_ms=int((time.monotonic() - t0) * 1000),
            )

        poll_n += 1
        await asyncio.sleep(_POLL_INTERVAL_S)
