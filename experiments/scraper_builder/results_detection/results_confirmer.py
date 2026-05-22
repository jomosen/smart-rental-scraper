"""LLM-based confirmation of what the results page actually shows."""
from __future__ import annotations

import json
from dataclasses import dataclass

from anthropic import AsyncAnthropic

from browser_session import BrowserSession
from form_capture.html_cleaner import clean_html_for_llm
from session_logger import SessionLogger

_MODEL = "claude-haiku-4-5-20251001"
_COST_IN = 0.80 / 1_000_000
_COST_OUT = 4.00 / 1_000_000
_MAX_HTML_CHARS = 10_240

_SYSTEM = """\
You are analyzing a car rental search results page.

Based on the HTML provided, classify what type of page this is.

Respond with a single JSON object. No markdown, no code blocks, no explanation.
The very first character of your response MUST be `{`.

{
  "page_type": "results" | "empty" | "validation_error" | "other",
  "rationale": "one sentence",
  "approx_vehicle_count": number | null
}

page_type values:
- "results"          — car offers with prices are shown (search returned vehicles)
- "empty"            — search completed but no vehicles are available for the dates/location
- "validation_error" — the form has an error preventing the search (bad date, missing field, etc.)
- "other"            — loading state, captcha, redirect, or any other unexpected page

approx_vehicle_count: estimate of distinct vehicle options shown (null if page_type is not "results")
"""


@dataclass
class ResultsConfirmation:
    page_type: str       # "results" | "empty" | "validation_error" | "other"
    rationale: str
    approx_vehicle_count: int | None


async def confirm_results(
    session: BrowserSession,
    logger: SessionLogger,
    llm_client: AsyncAnthropic,
) -> tuple[ResultsConfirmation, float]:
    """
    Capture current page HTML, clean it, truncate, and send to the LLM for
    one-shot page-type classification.

    Returns (ResultsConfirmation, cost_eur).
    Falls back to page_type="other" with an error rationale on any failure.
    """
    raw_html = await session.get_html()
    cleaned = clean_html_for_llm(raw_html)
    truncated = cleaned[:_MAX_HTML_CHARS]

    snapshot_path = logger.log_dir / "dom_snapshots" / "results_page_for_llm.html"
    snapshot_path.write_text(truncated, encoding="utf-8")

    try:
        response = await llm_client.messages.create(
            model=_MODEL,
            max_tokens=256,
            system=_SYSTEM,
            messages=[{"role": "user", "content": truncated}],
        )
        raw = response.content[0].text.strip()
        cost = (
            response.usage.input_tokens * _COST_IN
            + response.usage.output_tokens * _COST_OUT
        )

        llm_out_path = logger.log_dir / "llm_calls" / "results_confirmation.json"
        llm_out_path.write_text(
            json.dumps({"raw": raw, "cost_eur": cost}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        data = json.loads(raw)
        return ResultsConfirmation(
            page_type=data.get("page_type", "other"),
            rationale=data.get("rationale", ""),
            approx_vehicle_count=data.get("approx_vehicle_count"),
        ), cost

    except Exception as exc:
        logger.log("results_confirmer_error", error=str(exc))
        return ResultsConfirmation(
            page_type="other",
            rationale=f"LLM call failed: {exc}",
            approx_vehicle_count=None,
        ), 0.0
