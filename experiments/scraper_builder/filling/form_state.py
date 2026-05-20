"""Provider-agnostic form state capture, diff, and field verification."""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import date as _date
from pathlib import Path

from browser_session import BrowserSession
from field_analysis.field_identifier import IdentifiedField

_JS_CAPTURE = """
(formSelector) => {
    const els = document.querySelectorAll(
        formSelector + ' input, ' +
        formSelector + ' select, ' +
        formSelector + ' textarea, ' +
        formSelector + ' [role="combobox"]'
    );
    const counts = {};
    const result = {};
    for (const el of els) {
        const base = el.id || el.name || 'el';
        counts[base] = (counts[base] || 0) + 1;
        const key = counts[base] === 1 ? base : base + '_' + counts[base];
        result[key] = el.value !== undefined ? el.value : (el.textContent || '').trim();
    }
    return result;
}
"""


@dataclass
class FormInputSnapshot:
    inputs: dict[str, str]


@dataclass
class FormStateDiff:
    changed_inputs: dict[str, tuple[str, str]]
    added_inputs: dict[str, str]
    removed_inputs: dict[str, str]

    @property
    def has_changes(self) -> bool:
        return bool(self.changed_inputs or self.added_inputs)


async def capture_form_state(
    session: BrowserSession,
    form_selector: str,
) -> FormInputSnapshot:
    """
    Capture the current value of every input/select/textarea/combobox inside
    *form_selector*. Keys are element id, name, or 'el_N' as fallback.
    """
    data: dict[str, str] = await session.page.evaluate(_JS_CAPTURE, form_selector)
    return FormInputSnapshot(inputs=data)


def compare_states(
    before: FormInputSnapshot,
    after: FormInputSnapshot,
) -> FormStateDiff:
    changed: dict[str, tuple[str, str]] = {}
    added: dict[str, str] = {}
    removed: dict[str, str] = {}

    all_keys = set(before.inputs) | set(after.inputs)
    for key in all_keys:
        b = before.inputs.get(key)
        a = after.inputs.get(key)
        if b is None:
            added[key] = a
        elif a is None:
            removed[key] = b
        elif b != a:
            changed[key] = (b, a)

    return FormStateDiff(
        changed_inputs=changed,
        added_inputs=added,
        removed_inputs=removed,
    )


def save_snapshot(log_dir: Path, filename: str, snapshot) -> None:
    """Persist a FormInputSnapshot or FormStateDiff as JSON."""
    (log_dir / "dom_snapshots" / filename).write_text(
        json.dumps(asdict(snapshot), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


async def _read_field_display_value(
    session: BrowserSession,
    field: IdentifiedField,
) -> str | None:
    """
    Read the currently displayed value of a form field.

    Tries input_value() first (works for native inputs and selects).
    Falls back to reading the sibling display element inside the parent
    container, which is the react-select pattern where the selected text
    lives in a div rather than the hidden input's .value.
    """
    try:
        sel = f"xpath={field.selector}" if field.selector_type == "xpath" else field.selector
        loc = session.page.locator(sel).first

        raw = await loc.input_value(timeout=1_500)
        if raw and raw.strip():
            return raw.strip()

        # Fallback for react-select: the display text is in a sibling div
        # inside the value container (el.parentElement).
        text: str = await loc.evaluate("""
        el => {
            const parent = el.parentElement;
            if (!parent) return '';
            const display = parent.querySelector(
                '[class*="singleValue"], [class*="SingleValue"], [class*="single-value"]'
            );
            return display ? display.textContent.trim() : '';
        }
        """)
        return text.strip() if text and text.strip() else None
    except Exception:
        return None


async def is_field_at_target_date(
    session: BrowserSession,
    field: IdentifiedField,
    target: _date,
    date_format: str | None,
) -> bool:
    """
    Read the current value of *field* and check whether it represents *target*.

    Normalises format before comparing date objects so DD/MM/YYYY and
    YYYY-MM-DD are treated as the same date. Returns False if the field
    cannot be read or its value cannot be parsed.
    """
    from filling.date_utils import parse_date
    try:
        sel = f"xpath={field.selector}" if field.selector_type == "xpath" else field.selector
        raw_value = await session.page.locator(sel).first.input_value(timeout=2_000)
    except Exception:
        return False
    parsed = parse_date(raw_value, date_format)
    return parsed == target


async def is_field_at_target_time(
    session: BrowserSession,
    field: IdentifiedField,
    target_time: str,
) -> bool:
    """
    Read the current displayed value of a time field and check whether it
    represents *target_time*, normalizing both sides with normalize_time so
    "10:00", "10:00 AM", and "10h" are all treated as the same time.

    Returns False if the field cannot be read or the value cannot be parsed.
    """
    from filling.time_utils import normalize_time

    raw_value = await _read_field_display_value(session, field)
    if not raw_value:
        return False
    target_norm = normalize_time(target_time)
    field_norm = normalize_time(raw_value)
    if target_norm is None:
        return False
    return field_norm == target_norm
