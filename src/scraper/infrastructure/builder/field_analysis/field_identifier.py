"""LLM-based identification of all form fields."""
from __future__ import annotations

import json
from dataclasses import dataclass, replace as _dc_replace
from pathlib import Path

from anthropic import AsyncAnthropic
from bs4 import BeautifulSoup

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

REGLA CRÍTICA — elemento interactivo, no el de almacenamiento:
Muchos widgets modernos (combobox, autocomplete, dropdowns custom) tienen
DOS elementos en su contenedor:
  1. Un elemento VISIBLE e interactivo (con role="combobox", o un <input>
     visible, o un <button>) con el que el usuario hace click y escribe.
  2. Un <input type="hidden"> que solo ALMACENA el valor para el formulario.

SIEMPRE identifica el elemento INTERACTIVO (el visible), NUNCA el
input[type="hidden"]. El hidden no se puede clicar ni abrir.

Si el elemento interactivo no tiene id/name pero el hidden sí, prefiere
igualmente el interactivo: usa role, aria-label, o su posición dentro del
contenedor. Por ejemplo, prefiere `#container [role='combobox']` antes que
`input[name='x'][type='hidden']`.

REGLAS ADICIONALES:
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


# ── Post-processing: hidden-selector safety net ───────────────────────────────

def _is_selector_hidden(html: str, selector: str, selector_type: str) -> bool:
    """Return True if *selector* resolves to an input[type='hidden'] element."""
    if selector_type != "css":
        return False
    try:
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one(selector)
        if el is None:
            return False
        return el.get("type", "").lower() == "hidden"
    except Exception:
        return False


def _selector_for_element(el, container) -> str:
    """Build the most stable CSS selector for *el* within *container*."""
    if el.get("id"):
        return f"#{el['id']}"
    role = el.get("role", "")
    aria_label = el.get("aria-label", "")
    if role and aria_label:
        escaped = aria_label.replace('"', '\\"')
        return f"[role='{role}'][aria-label=\"{escaped}\"]"
    if role:
        return f"[role='{role}']"
    if aria_label:
        escaped = aria_label.replace('"', '\\"')
        return f"[aria-label=\"{escaped}\"]"
    container_id = container.get("id", "")
    if container_id:
        return f"#{container_id} {el.name}"
    return el.name


def _find_interactive_replacement(
    html: str, selector: str
) -> tuple[str, str] | None:
    """
    Find the interactive element in the same container as *selector*.
    Searches parent then grandparent, prioritising role=combobox >
    visible input > button/select. Returns (new_selector, element_kind) or None.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
        hidden_el = soup.select_one(selector)
        if hidden_el is None:
            return None

        ancestors = []
        p = hidden_el.parent
        if p:
            ancestors.append(p)
            if p.parent:
                ancestors.append(p.parent)

        for container in ancestors:
            for candidate in container.find_all(True):
                if candidate is hidden_el:
                    continue
                tag = getattr(candidate, "name", None)
                if not tag:
                    continue
                typ = candidate.get("type", "").lower()
                role = candidate.get("role", "")

                if tag == "input" and typ == "hidden":
                    continue

                if role == "combobox":
                    return _selector_for_element(candidate, container), tag

                if tag == "input" and typ not in (
                    "hidden", "submit", "reset", "button", "image"
                ):
                    return _selector_for_element(candidate, container), tag

                if tag in ("button", "select") or role in ("button", "listbox"):
                    return _selector_for_element(candidate, container), tag

        return None
    except Exception:
        return None


def _fix_hidden_field_selectors(
    fields: FormFields, form_html: str, log_dir: Path
) -> FormFields:
    """
    Safety net: any identified field whose selector resolves to an
    input[type='hidden'] is replaced by the interactive element in the
    same container. Provider-agnostic — uses HTML parsing only.
    """
    fixes: dict[str, IdentifiedField] = {}

    for name in _FIELD_NAMES:
        field = getattr(fields, name)
        if field is None:
            continue
        if not _is_selector_hidden(form_html, field.selector, field.selector_type):
            continue
        replacement = _find_interactive_replacement(form_html, field.selector)
        if replacement is None:
            continue
        new_selector, new_kind = replacement
        fixes[name] = _dc_replace(field, selector=new_selector, element_kind=new_kind)

    if not fixes:
        return fields

    fixes_log = log_dir / "llm_calls" / "hidden_selector_fixes.jsonl"
    with open(fixes_log, "a", encoding="utf-8") as f:
        for name, new_field in fixes.items():
            old = getattr(fields, name)
            f.write(json.dumps({
                "field": name,
                "old_selector": old.selector,
                "new_selector": new_field.selector,
                "new_kind": new_field.element_kind,
            }, ensure_ascii=False) + "\n")

    return FormFields(**{
        n: fixes.get(n, getattr(fields, n))
        for n in _FIELD_NAMES
    })


# ── LLM call ─────────────────────────────────────────────────────────────────

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
    Applies _fix_hidden_field_selectors as a post-processing safety net.
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

    # Safety net: replace any selector that points to a hidden element
    fields = _fix_hidden_field_selectors(fields, form_html, log_dir)

    return fields, cost_eur
