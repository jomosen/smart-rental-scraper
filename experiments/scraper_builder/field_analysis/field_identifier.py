"""LLM-based identification of all form fields."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from anthropic import AsyncAnthropic

_MODEL = "claude-sonnet-4-6"
_COST_IN = 3.00 * 0.90 / 1_000_000
_COST_OUT = 15.00 * 0.90 / 1_000_000

_SYSTEM = """\
Eres un agente que analiza formularios de búsqueda de sitios de alquiler de coches.

Te paso el HTML del formulario. Tu tarea es identificar cada campo del formulario
y devolver sus selectores.

La primera línea de tu respuesta debe empezar con `{`. No uses markdown, no uses
bloques de código, no uses comillas invertidas. Devuelve únicamente el JSON.

Estructura requerida:

{
  "pickup_location": {
    "selector": "css_selector",
    "selector_type": "css",
    "element_kind": "input|button|select|div|other",
    "rationale": "por qué este selector"
  },
  "pickup_date": {...} | null,
  "pickup_time": {...} | null,
  "return_location": {...} | null,
  "return_date": {...} | null,
  "return_time": {...} | null,
  "submit_button": {...} | null
}

REGLAS:
- "return_location" suele ser null si el sitio asume return = pickup.
- "submit_button" es el botón que ejecuta la búsqueda.
- Si un campo no se identifica con claridad, devolver null para ese campo.
- Prefiere selectores estables: id, name, data-attributes específicos.
- Evita selectores estructurales frágiles (nth-child, position).
- El selector debe ser único en el HTML provisto.
"""

_FIELD_NAMES = [
    "pickup_location", "pickup_date", "pickup_time",
    "return_location", "return_date", "return_time", "submit_button",
]


@dataclass
class IdentifiedField:
    selector: str
    selector_type: str
    element_kind: str
    rationale: str


@dataclass
class FormFields:
    pickup_location: IdentifiedField | None
    pickup_date: IdentifiedField | None
    pickup_time: IdentifiedField | None
    return_location: IdentifiedField | None
    return_date: IdentifiedField | None
    return_time: IdentifiedField | None
    submit_button: IdentifiedField | None


def _parse_json_response(raw: str) -> dict:
    """Extract a JSON object from *raw*, tolerating markdown code fences."""
    text = raw.strip()
    # Strip ```json ... ``` or ``` ... ``` fences
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[: text.rfind("```")]
    # Trim to the outermost { ... } in case of leading/trailing prose
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object found in LLM response: {raw[:200]!r}")
    return json.loads(text[start : end + 1])


def _parse_field(data: dict | None) -> IdentifiedField | None:
    if not data or data.get("selector") is None:
        return None
    return IdentifiedField(
        selector=data["selector"],
        selector_type=data.get("selector_type", "css"),
        element_kind=data.get("element_kind", "unknown"),
        rationale=data.get("rationale", ""),
    )


async def identify_form_fields(
    form_html: str,
    log_dir: Path,
    client: AsyncAnthropic,
) -> tuple[FormFields, float]:
    """
    Ask the LLM to identify all standard form fields.
    Saves call_001_field_identification_input/output to log_dir/llm_calls/.
    Returns (FormFields, cost_eur).
    """
    llm_calls = log_dir / "llm_calls"
    llm_calls.mkdir(exist_ok=True)
    (llm_calls / "call_001_field_identification_input.html").write_text(
        form_html, encoding="utf-8"
    )

    response = await client.messages.create(
        model=_MODEL,
        max_tokens=1024,
        temperature=0,
        system=_SYSTEM,
        messages=[{"role": "user", "content": f"HTML del formulario:\n\n{form_html}"}],
    )

    raw = response.content[0].text.strip()
    tokens_in = response.usage.input_tokens
    tokens_out = response.usage.output_tokens
    cost_eur = tokens_in * _COST_IN + tokens_out * _COST_OUT

    (llm_calls / "call_001_field_identification_output.json").write_text(
        json.dumps({"raw_response": raw, "tokens_input": tokens_in,
                    "tokens_output": tokens_out, "cost_eur": cost_eur},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    data = _parse_json_response(raw)
    fields = FormFields(**{name: _parse_field(data.get(name)) for name in _FIELD_NAMES})
    return fields, cost_eur
