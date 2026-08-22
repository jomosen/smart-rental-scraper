"""Auto-discover how a provider lets you change the search dates after the
first search, from the results page.

Level-2 companion to build_recipe. After a freshly-built recipe reaches the
results page, this figures out the cheapest refine strategy and VERIFIES it by
actually performing one date change:

  - 'navigate_and_change_dates' — an 'edit search' control leads to a distinct
    page that embeds the date form (e.g. centauro's /reserva/lugar-y-fechas/).
  - 'in_place' — the results page itself embeds the search form (same date/
    submit selectors), so dates can be changed without navigating away.
  - 'none' — neither could be confirmed; the recipe keeps full re-submit.

At most one LLM call (only when the form isn't already embedded in results).
Fully best-effort: any failure returns (None, 'none'); never raises.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import timedelta

from anthropic import AsyncAnthropic

from .browser_session import BrowserSession
from .recipe_executor import refine_dates
from ...domain.builder.models import Recipe

logger = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-6"
_MAX_HTML_CHARS = 24_000


def _strip(html: str) -> str:
    """Drop scripts/styles and cap length — the search summary sits near the top."""
    import re
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.S | re.I)
    html = re.sub(r"<style\b[^>]*>.*?</style>", "", html, flags=re.S | re.I)
    return html[:_MAX_HTML_CHARS]


_SYSTEM = """\
You are a web-scraping assistant. You are given the HTML of a car-rental RESULTS
page. Identify the single control that, when clicked, lets the user EDIT/MODIFY
the current search — it usually renders the current pickup/return dates (and
often the location) as a summary and either opens an edit panel or leads to an
'edit search' page.

Return ONLY a raw JSON object:
- "selector": a stable CSS selector (or XPath) for that clickable element, or
  null if the page has no such control.
- "selector_type": "css" or "xpath" (or null).
- "rationale": one short sentence.

No markdown fences, no extra text.
"""


async def _ask_llm(
    client: AsyncAnthropic, html: str, location: str
) -> tuple[str | None, str | None]:
    prompt = (
        f"The current search location is {location!r}.\n"
        f"Results page HTML (trimmed):\n\n{_strip(html)}\n\n"
        "Which element edits the search? Return the JSON object."
    )
    resp = await client.messages.create(
        model=_MODEL, max_tokens=256, system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    # Tolerate markdown fences around the object — same idiom as the other
    # builder LLM callers (see widget_classifier._parse_json_response).
    text = resp.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[: text.rfind("```")]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None, None  # best-effort probe: no JSON → no refine link
    data = json.loads(text[start : end + 1])
    return data.get("selector"), data.get("selector_type")


async def discover_refine_link(
    session: BrowserSession,
    recipe: Recipe,
    targets: dict,
    log_dir,
    client: AsyncAnthropic | None = None,
) -> tuple[str | None, str]:
    """Probe for the refine strategy. Precondition: *session* is on the
    provider's results page (a search has just been submitted).

    Returns (refine_url, strategy). The candidate is CONFIRMED by running one
    real date change (+7 days) and checking vehicles come back — so a detected
    form that isn't actually usable falls back to (None, 'none').
    """
    from dataclasses import replace

    pickup_rf = recipe.form_fields.get("pickup_date")
    if pickup_rf is None or not pickup_rf.selector:
        logger.info("[refine_discovery] recipe has no pickup_date selector — skipping")
        return None, "none"
    pickup_sel = pickup_rf.selector
    location = str(targets.get("location", ""))
    results_url = session.get_url()

    candidate_url: str | None = None
    candidate_strategy: str | None = None

    try:
        # ── A) Is the search form already embedded (visible) in results? ──────
        if await session.wait_for_selector(pickup_sel, timeout_ms=3_000):
            candidate_strategy = "in_place"
            logger.info("[refine_discovery] results page embeds the date field → in_place")

        # ── B) Otherwise ask the LLM for the edit control and click it. ───────
        else:
            client = client or AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
            selector, selector_type = await _ask_llm(client, await session.get_html(), location)
            if not selector:
                logger.info("[refine_discovery] LLM found no edit-search control")
                return None, "none"
            if not await session.click_selector(selector, selector_type or "css"):
                logger.info("[refine_discovery] could not click %r", selector)
                return None, "none"
            if not await session.wait_for_selector(pickup_sel, timeout_ms=8_000):
                logger.info("[refine_discovery] edit control did not reveal %r", pickup_sel)
                return None, "none"
            new_url = session.get_url()
            if new_url and new_url != results_url:
                candidate_url, candidate_strategy = new_url, "navigate_and_change_dates"
                logger.info("[refine_discovery] navigate deep-link candidate: %s", new_url)
            else:
                candidate_strategy = "in_place"
                logger.info("[refine_discovery] in-place edit panel (same URL) candidate")

        # ── Verify by performing one real date change (+7 days) ───────────────
        trial = replace(recipe, refine_strategy=candidate_strategy, refine_url=candidate_url)
        p2 = targets["pickup_date"] + timedelta(days=7)
        r2 = targets["return_date"] + timedelta(days=7)
        res = await refine_dates(session, trial, p2, r2, log_dir)
        if res.success:
            logger.info("[refine_discovery] CONFIRMED strategy=%s url=%s",
                        candidate_strategy, candidate_url)
            return candidate_url, candidate_strategy

        logger.info("[refine_discovery] candidate refine failed to verify "
                    "(phase=%s) — falling back to none", res.failed_phase)
        return None, "none"
    except Exception as exc:
        logger.info("[refine_discovery] probe failed: %s", exc)
        return None, "none"
