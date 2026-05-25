"""Vía 1: LLM extracts the full vehicle list directly from page HTML (reference truth)."""
from __future__ import annotations

import json
from pathlib import Path

from anthropic import AsyncAnthropic

from extraction.models import VehicleResult
from form_capture.html_cleaner import clean_html_for_llm
from session_logger import SessionLogger

_MODEL = "claude-sonnet-4-6"
_COST_IN = 3.00 * 0.90 / 1_000_000
_COST_OUT = 15.00 * 0.90 / 1_000_000
_MAX_HTML_CHARS = 60_000

_SYSTEM = """\
Eres un agente que extrae la lista de vehículos de una página de resultados
de alquiler de coches.

Te paso el HTML (limpio y reducido) de la página de resultados.
Extrae TODOS los vehículos que veas. Para cada uno devuelve los campos que
puedas identificar (null si no aparece o es ambiguo):

- model: nombre del modelo del vehículo (ej. "FIAT 500")
- group_code: código de grupo del provider (ej. "Grupo A", "S1A")
- availability_note: "o similar" si es aproximado, "garantizado" si el
  modelo está garantizado, null si no se indica
- category: categoría/tier del provider (ej. "Económico", "Familiar")
- transmission: como aparezca ("M", "A", "Manual", "Automático")
- seats, doors, bags: números enteros si se identifican, null si ambiguo
- rate_type: tipo de tarifa (ej. "Premium", "Básica", "Alquiler Premium")
- price_final: el precio que el cliente PAGA realmente (con descuento si
  lo hay). Si un atributo como aria-label expone el precio limpio, úsalo
  como fuente fiable.
- price_original: precio tachado/original si existe, si no null
- currency: código de moneda ("EUR", "USD", etc.) o símbolo si no hay código
- discount_pct: porcentaje de descuento como número (ej. 15.0), null si no hay

REGLA DE PRECIO CRÍTICA: price_final es lo que el cliente PAGA. Si hay un
precio tachado (precio original) y un precio final (con descuento), price_final
es el menor/final. Si un atributo aria-label contiene el precio de forma limpia
(ej. "FIAT 500: 273,00 €. Descuento: 15%."), usa ese valor para price_final.

Devuelve ÚNICAMENTE un JSON con este formato:
{"vehicles": [{"model": "...", "group_code": "...", ...}, ...]}

Sin markdown, sin bloques de código. La primera línea de tu respuesta debe
empezar con `{`.
"""


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


def _parse_vehicle(raw: dict) -> VehicleResult:
    def _int(v) -> int | None:
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def _float(v) -> float | None:
        try:
            if v is None:
                return None
            if isinstance(v, str):
                v = v.replace(",", ".").replace(" ", "")
            return float(v)
        except (TypeError, ValueError):
            return None

    return VehicleResult(
        model=raw.get("model") or None,
        group_code=raw.get("group_code") or None,
        availability_note=raw.get("availability_note") or None,
        category=raw.get("category") or None,
        transmission=raw.get("transmission") or None,
        seats=_int(raw.get("seats")),
        doors=_int(raw.get("doors")),
        bags=_int(raw.get("bags")),
        rate_type=raw.get("rate_type") or None,
        price_final=_float(raw.get("price_final")),
        price_original=_float(raw.get("price_original")),
        currency=raw.get("currency") or None,
        discount_pct=_float(raw.get("discount_pct")),
    )


async def extract_vehicles_llm(
    results_html: str,
    logger: SessionLogger,
    llm_client: AsyncAnthropic,
) -> tuple[list[VehicleResult], float]:
    """
    Clean *results_html*, send to LLM, parse vehicle list.

    This is the reference truth extraction — do not truncate aggressively:
    we need all vehicles. Uses a 60KB limit; if the page exceeds this,
    the first 60KB is sent (assumption: cards appear early in DOM order).

    Returns (list[VehicleResult], cost_eur).
    """
    cleaned = clean_html_for_llm(results_html)
    truncated = cleaned[:_MAX_HTML_CHARS]

    (logger.log_dir / "dom_snapshots" / "vehicles_llm_input.html").write_text(
        truncated, encoding="utf-8"
    )

    response = await llm_client.messages.create(
        model=_MODEL,
        max_tokens=4096,
        system=_SYSTEM,
        messages=[{"role": "user", "content": truncated}],
    )
    raw = response.content[0].text.strip()
    cost = (
        response.usage.input_tokens * _COST_IN
        + response.usage.output_tokens * _COST_OUT
    )

    (logger.log_dir / "llm_calls" / "vehicles_llm_output.json").write_text(
        json.dumps({"raw": raw, "cost_eur": cost}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    data = _parse_json_response(raw)
    vehicles = [_parse_vehicle(v) for v in data.get("vehicles", [])]

    logger.log("llm_extraction", count=len(vehicles), cost_eur=cost)
    return vehicles, cost
