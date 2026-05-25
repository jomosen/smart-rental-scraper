"""Vía 2 (step 1): LLM discovers the CSS structure of the results page."""
from __future__ import annotations

import json

from anthropic import AsyncAnthropic

from extraction.models import FieldSelector, ResultsStructure
from form_capture.html_cleaner import clean_html_for_llm
from session_logger import SessionLogger

_MODEL = "claude-sonnet-4-6"
_COST_IN = 3.00 * 0.90 / 1_000_000
_COST_OUT = 15.00 * 0.90 / 1_000_000
_MAX_HTML_CHARS = 60_000

_SYSTEM = """\
Eres un agente que analiza la ESTRUCTURA de una página de resultados de
alquiler de coches para extraer datos de forma repetible y determinista.

Te paso el HTML limpio. Identifica:

1. vehicle_card_selector: el selector CSS que matchea CADA tarjeta de
   vehículo (el elemento repetido que contiene un vehículo completo).
   Debe coincidir con N tarjetas (una por vehículo), no con el contenedor.

2. field_selectors: para cada campo, un selector RELATIVO a la tarjeta y
   cómo extraerlo. Incluye los campos que puedas identificar:
   model, group_code, availability_note, transmission, seats,
   price_final, currency.

   Para seats y transmission: PRIORIZA aria-labels en <span> (los SVGs de
   iconos han sido sustituidos por <span aria-label="..."> en este HTML).
   Ejemplo: <span aria-label="Nº de plazas: 5"> → seats con
   "regex:\\d+" sobre "attribute:aria-label". Si hay aria-label con
   "automático"/"manual"/"automatic", úsalo para transmission.

   extraction puede ser:
   - "text"              → usar textContent del elemento
   - "attribute:ATTR"    → usar el valor del atributo ATTR
   - "regex:PATRON"      → aplicar regex al textContent, devolver grupo 1

3. price_strategy: describe cómo obtener el precio final que paga el
   cliente (si hay precio original tachado y descuento, ¿cuál es el final?
   ¿viene de un aria-label, del último span, etc.?).

4. rationale: explica brevemente por qué elegiste ese vehicle_card_selector.

REGLAS CLAVE:
- vehicle_card_selector debe ser lo suficientemente específico para matchear
  tarjetas individuales, NO el contenedor de todas las tarjetas.
- Los field_selectors son RELATIVOS a la tarjeta (se aplicarán dentro de
  cada elemento que matchee vehicle_card_selector).
- PRIORIZA fuentes semánticas estables: atributos aria-label, id semánticos,
  roles, estructura HTML (h2, h3, li, span) antes que clases CSS.
- Si el precio está limpio en un atributo (ej. aria-label="FIAT 500: 273,00 €"),
  usa "attribute:aria-label" para price_final, es más fiable que parsear spans.
- Para campos numéricos (seats, doors, bags) en listas sin etiquetas claras,
  usa el contexto (orden, aria-labels, iconos cercanos) o deja rationale
  explicando la ambigüedad.
- NO uses selectores que dependan de clases auto-generadas o frágiles si
  hay alternativas semánticas disponibles.

Devuelve ÚNICAMENTE un JSON con este formato exacto:
{
  "vehicle_card_selector": "selector css",
  "field_selectors": [
    {"field": "model", "selector": "...", "extraction": "text", "rationale": "..."},
    ...
  ],
  "price_strategy": "descripción",
  "rationale": "por qué este card selector"
}

Sin markdown, sin bloques de código. La primera línea debe empezar con `{`.
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


async def classify_results_structure(
    results_html: str,
    logger: SessionLogger,
    llm_client: AsyncAnthropic,
    container_html: str | None = None,
) -> tuple[ResultsStructure, float]:
    """
    Ask the LLM to identify the CSS structure of the vehicle-list.

    When *container_html* is provided (isolated vehicle-list container from the
    live DOM), it is used instead of *results_html* — more compact, no truncation.
    Falls back to *results_html* truncated to _MAX_HTML_CHARS.

    Returns (ResultsStructure, cost_eur).
    """
    if container_html is not None:
        cleaned = clean_html_for_llm(container_html, preserve_svg_aria=True)
        truncated = cleaned          # container is compact; never truncate
    else:
        cleaned = clean_html_for_llm(results_html, preserve_svg_aria=True)
        truncated = cleaned[:_MAX_HTML_CHARS]

    response = await llm_client.messages.create(
        model=_MODEL,
        max_tokens=2048,
        system=_SYSTEM,
        messages=[{"role": "user", "content": truncated}],
    )
    raw = response.content[0].text.strip()
    cost = (
        response.usage.input_tokens * _COST_IN
        + response.usage.output_tokens * _COST_OUT
    )

    (logger.log_dir / "llm_calls" / "structure_classification.json").write_text(
        json.dumps({"raw": raw, "cost_eur": cost}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    data = _parse_json_response(raw)

    field_selectors = [
        FieldSelector(
            field=fs.get("field", ""),
            selector=fs.get("selector", ""),
            extraction=fs.get("extraction", "text"),
            rationale=fs.get("rationale", ""),
        )
        for fs in data.get("field_selectors", [])
        if fs.get("field") and fs.get("selector")
    ]

    structure = ResultsStructure(
        vehicle_card_selector=data.get("vehicle_card_selector", ""),
        field_selectors=field_selectors,
        price_strategy=data.get("price_strategy", ""),
        rationale=data.get("rationale", ""),
    )

    logger.log(
        "structure_classified",
        card_selector=structure.vehicle_card_selector,
        field_count=len(structure.field_selectors),
        price_strategy=structure.price_strategy,
        cost_eur=cost,
    )
    return structure, cost
