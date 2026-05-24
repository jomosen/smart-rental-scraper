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
_MAX_RETRIES = 1

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
    page_type: str           # "results" | "empty" | "validation_error" | "other" | "confirmer_error"
    rationale: str
    approx_vehicle_count: int | None
    error: str | None = None  # set only when page_type="confirmer_error"


def _parse_json_response(raw: str) -> dict:
    """Extract a JSON object from *raw*, tolerating markdown code fences."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[: text.rfind("```")]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object found in LLM response: {raw[:200]!r}")
    return json.loads(text[start : end + 1])


async def _call_llm(
    llm_client: AsyncAnthropic,
    truncated: str,
) -> tuple[dict, float]:
    """Make one LLM call; return (parsed_dict, cost_eur)."""
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
    return _parse_json_response(raw), cost


async def confirm_results(
    session: BrowserSession,
    logger: SessionLogger,
    llm_client: AsyncAnthropic,
) -> tuple[ResultsConfirmation, float]:
    """
    Capture current page HTML, clean and truncate it, then call the LLM
    (with one retry on parse failure) to classify the page type.

    Returns (ResultsConfirmation, cost_eur).
    On persistent failure, returns page_type="confirmer_error" with the
    error detail in the `error` field — never silently masks errors as
    page_type="other".
    """
    raw_html = await session.get_html()
    cleaned = clean_html_for_llm(raw_html)
    truncated = cleaned[:_MAX_HTML_CHARS]

    snapshot_path = logger.log_dir / "dom_snapshots" / "results_page_for_llm.html"
    snapshot_path.write_text(truncated, encoding="utf-8")

    total_cost = 0.0
    last_error: str | None = None

    for attempt in range(_MAX_RETRIES + 1):
        try:
            data, cost = await _call_llm(llm_client, truncated)
            total_cost += cost

            llm_out_path = logger.log_dir / "llm_calls" / "results_confirmation.json"
            llm_out_path.write_text(
                json.dumps({"data": data, "cost_eur": cost}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            return ResultsConfirmation(
                page_type=data.get("page_type", "other"),
                rationale=data.get("rationale", ""),
                approx_vehicle_count=data.get("approx_vehicle_count"),
                error=None,
            ), total_cost

        except Exception as exc:
            last_error = str(exc)
            logger.log(
                "results_confirmer_attempt_failed",
                attempt=attempt,
                error=last_error,
            )

    logger.log(
        "results_confirmer_failed",
        error=last_error,
        attempts=_MAX_RETRIES + 1,
    )
    return ResultsConfirmation(
        page_type="confirmer_error",
        rationale=f"LLM classification failed after {_MAX_RETRIES + 1} attempts",
        approx_vehicle_count=None,
        error=last_error,
    ), total_cost
