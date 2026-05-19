"""Provider-agnostic form state capture and diff."""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

from browser_session import BrowserSession

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
