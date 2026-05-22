"""Heuristic poller: wait until the results page is ready after form submit."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from browser_session import BrowserSession
from session_logger import SessionLogger

_POLL_INTERVAL_S = 0.7
_PRICE_ELEMENT_THRESHOLD = 3

# CSS selectors / class fragments used to count candidate price elements
_PRICE_SELECTORS = ",".join([
    '[class*="price"]',
    '[class*="rate"]',
    '[class*="tarifa"]',
    '[class*="amount"]',
    '[class*="cost"]',
    '[class*="precio"]',
    '[class*="importe"]',
])

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
    signal: str          # "url_changed" | "result_elements" | "empty_message" | "timeout"
    url_before: str
    url_after: str
    candidate_count: int
    spinner_gone: bool
    waited_ms: int


# ── JS helpers ────────────────────────────────────────────────────────────────

async def _count_price_like_elements(session: BrowserSession) -> int:
    """Count visible elements whose class hints at price display."""
    try:
        count: int = await session.page.evaluate(
            """(selectors) => {
                const els = document.querySelectorAll(selectors);
                let n = 0;
                const priceRe = /\\d[\\d.,]+/;
                for (const el of els) {
                    if (el.offsetParent !== null && priceRe.test(el.textContent)) n++;
                }
                return n;
            }""",
            _PRICE_SELECTORS,
        )
        return count
    except Exception:
        return 0


async def _has_visible_spinner(session: BrowserSession) -> bool:
    """Return True if any ARIA or CSS spinner is currently visible."""
    try:
        has_spinner: bool = await session.page.evaluate(
            """(fragments) => {
                // ARIA-based
                const ariaEls = document.querySelectorAll(
                    '[role="progressbar"],[aria-busy="true"]'
                );
                for (const el of ariaEls) {
                    if (el.offsetParent !== null) return true;
                }
                // Class-based
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


# ── Main waiter ───────────────────────────────────────────────────────────────

async def wait_for_results(
    session: BrowserSession,
    url_before_submit: str,
    logger: SessionLogger,
    timeout_ms: int = 30_000,
) -> WaitOutcome:
    """
    Poll until results are ready or timeout.

    Signals (first detected wins):
      - "url_changed"     — navigation to a new URL occurred
      - "result_elements" — ≥3 price-bearing elements are visible
      - "empty_message"   — a multilingual "no results" phrase was detected

    Returns ready=True only when a signal fires AND no spinner is visible.
    Returns ready=False with signal="timeout" after *timeout_ms* milliseconds.
    """
    signal: str | None = None
    candidate_count = 0
    spinner_gone = True
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
        candidate_count = await _count_price_like_elements(session)
        has_empty = await _has_empty_results_message(session)
        spinner_gone = not await _has_visible_spinner(session)

        if signal is None:
            if current_url != url_before_submit:
                signal = "url_changed"
            elif candidate_count >= _PRICE_ELEMENT_THRESHOLD:
                signal = "result_elements"
            elif has_empty:
                signal = "empty_message"

        logger.log(
            "results_wait_poll",
            poll=poll_n,
            elapsed_ms=elapsed_ms,
            signal=signal,
            url_changed=(current_url != url_before_submit),
            candidate_count=candidate_count,
            has_empty=has_empty,
            spinner_gone=spinner_gone,
        )

        if signal is not None and spinner_gone:
            return WaitOutcome(
                ready=True,
                signal=signal,
                url_before=url_before_submit,
                url_after=current_url,
                candidate_count=candidate_count,
                spinner_gone=True,
                waited_ms=elapsed_ms,
            )

        poll_n += 1
        await asyncio.sleep(_POLL_INTERVAL_S)
