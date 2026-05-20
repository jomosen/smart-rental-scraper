"""LLM-based classification of date picker widgets."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from anthropic import AsyncAnthropic

_MODEL = "claude-sonnet-4-6"
_COST_IN = 3.00 * 0.90 / 1_000_000
_COST_OUT = 15.00 * 0.90 / 1_000_000

_SYSTEM = """\
Eres un agente que analiza widgets de selección de fecha en formularios web.

Te paso:
- El HTML del campo de fecha ANTES de hacer click.
- El HTML de la página DESPUÉS de hacer click sobre el campo.

Clasifica el tipo de widget de fecha:
- "text_input": input de texto que acepta escribir la fecha directamente.
  Tras click NO aparece calendario (o aparece pero también acepta texto).
- "native_date": <input type="date"> nativo del navegador.
- "custom_calendar": tras click aparece un calendario custom (grid de
  días) que requiere navegación visual. No acepta (o no fiablemente)
  escritura directa.
- "unknown": no se puede determinar.

La primera línea de tu respuesta debe empezar con `{`. No uses markdown,
no uses bloques de código. Devuelve únicamente el JSON.

Estructura requerida:
{
  "widget_type": "text_input|native_date|custom_calendar|unknown",
  "accepts_direct_typing": true|false,
  "date_format": "DD/MM/YYYY"|"YYYY-MM-DD"|"MM/DD/YYYY"|null,
  "calendar_container_selector": "..."|null,
  "next_month_selector": "..."|null,
  "prev_month_selector": "..."|null,
  "day_cell_selector": "..."|null,
  "month_year_label_selector": "..."|null,
  "is_range_calendar": true|false,
  "rationale": "..."
}

REGLAS:
- date_format: dedúcelo del value actual del input si lo tiene
  (ej. value="18/05/2026" -> "DD/MM/YYYY").
- accepts_direct_typing: true si el campo acepta teclado aunque tenga calendario.
- Para custom_calendar, day_cell_selector debe coincidir con las celdas de
  día clicables (típicamente td, button, o div con el número del día).
- next_month_selector y prev_month_selector: botones de navegación entre meses.
  Identifícalos por aria-label, data-*, o clase semántica; evita :nth-child.
- month_year_label_selector: elemento que muestra "Mayo 2026" o similar.
- is_range_calendar: true si el mismo calendario gestiona inicio y fin.
- Prefiere selectores estables. Evita clases auto-generadas de CSS-in-JS.
"""


def _parse_json_response(raw: str) -> dict:
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


@dataclass
class DateWidgetInfo:
    widget_type: str
    accepts_direct_typing: bool
    date_format: str | None
    calendar_container_selector: str | None
    next_month_selector: str | None
    prev_month_selector: str | None
    day_cell_selector: str | None
    month_year_label_selector: str | None
    is_range_calendar: bool
    rationale: str


async def classify_date_widget(
    field_html_before: str,
    page_html_after_click: str,
    label: str,
    log_dir: Path,
    client: AsyncAnthropic,
) -> tuple[DateWidgetInfo, float]:
    """
    Ask the LLM to classify a date field widget.

    *label* is used to name the saved files (e.g. "date_pickup_widget").
    Saves call_{label}_input.html and call_{label}_output.json to
    log_dir/llm_calls/. Returns (DateWidgetInfo, cost_eur).
    """
    llm_calls = log_dir / "llm_calls"
    llm_calls.mkdir(exist_ok=True)

    user_content = (
        "=== HTML DEL CAMPO ANTES DEL CLICK ===\n\n"
        f"{field_html_before}\n\n"
        "=== HTML DE LA PÁGINA DESPUÉS DEL CLICK ===\n\n"
        f"{page_html_after_click}"
    )
    (llm_calls / f"call_{label}_input.html").write_text(user_content, encoding="utf-8")

    response = await client.messages.create(
        model=_MODEL,
        max_tokens=768,
        temperature=0,
        system=_SYSTEM,
        messages=[{"role": "user", "content": user_content}],
    )

    raw = response.content[0].text.strip()
    tokens_in = response.usage.input_tokens
    tokens_out = response.usage.output_tokens
    cost_eur = tokens_in * _COST_IN + tokens_out * _COST_OUT

    (llm_calls / f"call_{label}_output.json").write_text(
        json.dumps(
            {"raw_response": raw, "tokens_input": tokens_in,
             "tokens_output": tokens_out, "cost_eur": cost_eur},
            indent=2, ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    data = _parse_json_response(raw)
    widget = DateWidgetInfo(
        widget_type=data.get("widget_type", "unknown"),
        accepts_direct_typing=bool(data.get("accepts_direct_typing", False)),
        date_format=data.get("date_format"),
        calendar_container_selector=data.get("calendar_container_selector"),
        next_month_selector=data.get("next_month_selector"),
        prev_month_selector=data.get("prev_month_selector"),
        day_cell_selector=data.get("day_cell_selector"),
        month_year_label_selector=data.get("month_year_label_selector"),
        is_range_calendar=bool(data.get("is_range_calendar", False)),
        rationale=data.get("rationale", ""),
    )
    return widget, cost_eur
