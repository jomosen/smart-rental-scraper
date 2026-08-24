"""Semantic LLM resolver for UNKNOWN models (engine v2 fallback, spec §31).

The LLM never returns an ACRISS code. It returns a structured semantic profile
per unknown model — likely category letter, likely body-type letter, and a
powertrain profile mode — and the deterministic engine builds (or refuses to
build) the code. Suggestions feed the review queue (§32); they are NEVER
auto-promoted to the model dictionary (§33).
"""
from __future__ import annotations

import json
import logging
import os

from google import genai
from google.genai import types

from .gemini_service import flash_model

logger = logging.getLogger(__name__)

# Bump when this prompt changes — part of classifier_version.
RESOLVER_PROMPT_VERSION = "1"

_SYSTEM = """\
You are an automotive market analyst. For each vehicle model listed you return
a SEMANTIC PROFILE — never a final ACRISS code.

Return ONLY a raw JSON array, one object per input line, same order:
{
  "likely_category": one ACRISS category letter
      (M,N,E,H,C,D,I,J,S,R,F,G,P,U,L,W) or null if you cannot judge,
  "likely_type": one ACRISS body-type letter
      (B,C,D,W,V,L,S,T,F,J,E,M,N,G,K) or null,
  "powertrain_profile": one of
      "ice_only","ice_dominant","bev_only","hybrid_only","phev_only","mixed",
  "confidence": 0.0-1.0,
  "reason": one short sentence
}

Rules:
- Category reflects the model's size/positioning, NEVER just the brand
  (premium brand does not imply Premium category).
- G = crossover (car-platform, road-oriented); F = traditional/utility SUV.
- V = passenger van ONLY for real people-carriers; K = commercial/cargo van.
- Never invent high confidence: unfamiliar model -> low confidence.
No markdown fences, no extra text.
"""


class SemanticModelResolver:
    """Batch resolver: list of raw model names → list of profile dicts (or None)."""

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.getenv("GEMINI_API_KEY")
        self._client = genai.Client(api_key=self._api_key) if self._api_key else None

    def resolve(self, raw_models: list[str]) -> list[dict | None]:
        if not raw_models:
            return []
        if self._client is None:
            logger.warning("SemanticModelResolver: no GEMINI_API_KEY — skipping")
            return [None] * len(raw_models)
        prompt = "Models:\n" + "\n".join(f"{i+1}. {m}" for i, m in enumerate(raw_models))
        try:
            response = self._client.models.generate_content(
                model=flash_model(),
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=_SYSTEM,
                    response_mime_type="application/json",
                ),
            )
            text = (response.text or "").strip()
            start = text.find("[")
            if start == -1:
                return [None] * len(raw_models)
            data = json.JSONDecoder().raw_decode(text[start:])[0]
        except Exception as exc:  # noqa: BLE001 — resolver is strictly best-effort
            logger.warning("SemanticModelResolver failed: %s", exc)
            return [None] * len(raw_models)

        out: list[dict | None] = []
        for i in range(len(raw_models)):
            item = data[i] if i < len(data) and isinstance(data[i], dict) else None
            out.append(item)
        return out
