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
- transmission: tipo de cambio ("M"=manual, "A"=automático, o como aparezca).
  Busca aria-labels o atributos title con palabras como "automático",
  "manual", "automatic". Si no hay indicación clara, devuelve null.
- seats: número de plazas como entero. Busca aria-labels tipo
  "Nº de plazas: 5" o "5 seats". Si no aparece con claridad, devuelve null.
- price_final: el precio que el cliente PAGA realmente (con descuento si
  lo hay). Si un atributo aria-label expone el precio limpio (ej.
  "FIAT 500: 273,00 €"), úsalo como fuente fiable.
- currency: código de moneda ("EUR", "USD", etc.) o símbolo si no hay código.

REGLA DE PRECIO CRÍTICA: price_final es lo que el cliente PAGA. Si hay un
precio tachado y un precio con descuento, price_final es el menor. Si un
atributo aria-label contiene el precio (ej. "FIAT 500: 273,00 €. Descuento:
15%."), usa ese valor para price_final.

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
        transmission=raw.get("transmission") or None,
        seats=_int(raw.get("seats")),
        price_final=_float(raw.get("price_final")),
        currency=raw.get("currency") or None,
    )


async def extract_vehicles_llm(
    results_html: str,
    logger: SessionLogger,
    llm_client: AsyncAnthropic,
    container_html: str | None = None,
) -> tuple[list[VehicleResult], float]:
    """
    Clean HTML, send to LLM, parse vehicle list.

    When *container_html* is provided (the isolated vehicle-list container from the
    live DOM), it is used instead of *results_html*. The container is far more compact
    than the full page, so all vehicles fit without truncation.

    Falls back to *results_html* truncated to _MAX_HTML_CHARS when no container is
    available (original behaviour, kept for safety).

    Returns (list[VehicleResult], cost_eur).
    """
    if container_html is not None:
        cleaned = clean_html_for_llm(container_html, preserve_svg_aria=True)
        input_html = cleaned          # container should fit; never truncate
        source = "container"
    else:
        cleaned = clean_html_for_llm(results_html, preserve_svg_aria=True)
        input_html = cleaned[:_MAX_HTML_CHARS]
        source = "full_page"

    logger.log("llm_extraction_input", source=source, chars=len(input_html))

    (logger.log_dir / "dom_snapshots" / "vehicles_llm_input.html").write_text(
        input_html, encoding="utf-8"
    )

    response = await llm_client.messages.create(
        model=_MODEL,
        max_tokens=4096,
        system=_SYSTEM,
        messages=[{"role": "user", "content": input_html}],
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
