"""LLM-based widget classification after clicking a form field."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from anthropic import AsyncAnthropic

_MODEL = "claude-sonnet-4-6"
_COST_IN = 3.00 * 0.90 / 1_000_000
_COST_OUT = 15.00 * 0.90 / 1_000_000

_SYSTEM = """\
Eres un agente que analiza widgets interactivos de formularios web.

Te paso:
- El HTML del campo ANTES de hacer click sobre él.
- El HTML de la página DESPUÉS de hacer click (lo que ha cambiado o aparecido).

Tu tarea es clasificar qué tipo de widget es este campo.

Tipos posibles:
- "native_select": un <select> HTML estándar con <option> hijos.
- "datalist": un <input list="..."> con <datalist>.
- "custom_dropdown": un <div> que muestra una lista de opciones tras click.
- "custom_modal": un modal/overlay que apareció tras click.
- "autocomplete": un input que requiere escribir para mostrar sugerencias.
- "unknown": no se puede determinar.

Devuelve JSON (sin markdown, solo el objeto):
{
  "widget_type": "native_select|datalist|custom_dropdown|custom_modal|autocomplete|unknown",
  "options_container_selector": "selector_que_contiene_las_opciones_or_null",
  "option_item_selector": "selector_de_cada_opcion_or_null",
  "is_searchable": true|false,
  "rationale": "explicación breve"
}

REGLAS:
- "is_searchable": true si requiere escribir texto para filtrar opciones.
- Si el widget no muestra opciones tras click (autocomplete puro), los
  selectores pueden ser null.
- Prefiere selectores estables: id, data-*, class semántica.
"""


@dataclass
class WidgetInfo:
    widget_type: str
    options_container_selector: str | None
    option_item_selector: str | None
    is_searchable: bool
    rationale: str


async def classify_widget(
    field_html_before: str,
    page_html_after: str,
    log_dir: Path,
    client: AsyncAnthropic,
) -> tuple[WidgetInfo, float]:
    """
    Ask the LLM to classify the widget that appeared after clicking a field.
    Saves call_002_widget_classification_input/output to log_dir/llm_calls/.
    Returns (WidgetInfo, cost_eur).
    """
    llm_calls = log_dir / "llm_calls"
    llm_calls.mkdir(exist_ok=True)

    user_content = (
        "=== HTML DEL CAMPO ANTES DEL CLICK ===\n\n"
        f"{field_html_before}\n\n"
        "=== HTML DE LA PÁGINA DESPUÉS DEL CLICK ===\n\n"
        f"{page_html_after}"
    )
    (llm_calls / "call_002_widget_classification_input.html").write_text(
        user_content, encoding="utf-8"
    )

    response = await client.messages.create(
        model=_MODEL,
        max_tokens=512,
        system=_SYSTEM,
        messages=[{"role": "user", "content": user_content}],
    )

    raw = response.content[0].text.strip()
    tokens_in = response.usage.input_tokens
    tokens_out = response.usage.output_tokens
    cost_eur = tokens_in * _COST_IN + tokens_out * _COST_OUT

    (llm_calls / "call_002_widget_classification_output.json").write_text(
        json.dumps({"raw_response": raw, "tokens_input": tokens_in,
                    "tokens_output": tokens_out, "cost_eur": cost_eur},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    data = json.loads(raw)
    widget = WidgetInfo(
        widget_type=data.get("widget_type", "unknown"),
        options_container_selector=data.get("options_container_selector"),
        option_item_selector=data.get("option_item_selector"),
        is_searchable=bool(data.get("is_searchable", False)),
        rationale=data.get("rationale", ""),
    )
    return widget, cost_eur
