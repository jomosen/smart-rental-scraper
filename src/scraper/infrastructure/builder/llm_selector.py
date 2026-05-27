"""Ask Claude Sonnet 4.6 which selector to click to close the cookie banner."""
from __future__ import annotations

import json
import os

import anthropic

from .models import LLMDecision

_MODEL = "claude-sonnet-4-6"
# EUR per token (approximate: $3/$15 per MTok × 0.90 EUR/USD)
_COST_INPUT_PER_TOKEN = 3.00 * 0.90 / 1_000_000
_COST_OUTPUT_PER_TOKEN = 15.00 * 0.90 / 1_000_000

_SYSTEM = """\
You are an expert web scraper assistant. Your job is to identify the CSS selector
or XPath expression that, when clicked, will close or accept a cookie consent banner
on a website.

You will receive HTML snippets of candidate elements. Analyse them and return a JSON
object with these fields:
- "selector": the CSS or XPath expression (string, or null if no banner found)
- "selector_type": "css" or "xpath" (or null if no banner found)
- "rationale": brief explanation (1-2 sentences)

Return ONLY the raw JSON object. No markdown fences, no extra text.
"""


def _build_prompt(candidates: list[dict]) -> str:
    lines = ["Here are the top candidate HTML snippets from the page:\n"]
    for i, c in enumerate(candidates, 1):
        lines.append(f"--- Candidate {i} (tag={c['tag']}, score={c['score']}) ---")
        lines.append(c["html"])
        lines.append("")
    lines.append(
        "Which element should be clicked to close/accept the cookie banner? "
        "Return a selector that targets the BUTTON or LINK inside the banner. "
        "If no cookie banner is present, set selector to null."
    )
    return "\n".join(lines)


async def ask_llm(candidates: list[dict]) -> tuple[LLMDecision, float]:
    """
    Query the LLM and return (LLMDecision, cost_eur).
    Raises if the API call fails or the response is not valid JSON.
    """
    client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    response = await client.messages.create(
        model=_MODEL,
        max_tokens=256,
        system=_SYSTEM,
        messages=[{"role": "user", "content": _build_prompt(candidates)}],
    )

    raw = response.content[0].text.strip()
    data = json.loads(raw)

    tokens_in = response.usage.input_tokens
    tokens_out = response.usage.output_tokens
    cost_eur = tokens_in * _COST_INPUT_PER_TOKEN + tokens_out * _COST_OUTPUT_PER_TOKEN

    decision = LLMDecision(
        selector=data.get("selector"),
        selector_type=data.get("selector_type"),
        rationale=data.get("rationale", ""),
        tokens_input=tokens_in,
        tokens_output=tokens_out,
    )
    return decision, cost_eur
