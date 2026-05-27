"""Orchestrator for Experiment 3: navigate → explore → fill location → verify state diff."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

from anthropic import AsyncAnthropic

from .browser_session import BrowserSession
from .filling.base_filler import FillResult
from .filling.filler_factory import UnsupportedWidgetError, get_filler_for_widget
from .filling.form_state import (
    FormInputSnapshot,
    FormStateDiff,
    capture_form_state,
    compare_states,
    save_snapshot as save_state_snapshot,
)
from .location_explorer import (
    LocationExplorationReport,
    explore_location_field_in_session,
)
from .session_logger import SessionLogger

_BASE_LOGS = Path(__file__).parent / "logs"


# ── Log helpers ──────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _log(log_dir: Path, event_type: str, **kwargs) -> None:
    event = {"ts": _now(), "type": event_type, **kwargs}
    with open(log_dir / "trace.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def create_fill_log_dir(site_name: str) -> Path:
    """Create and return logs/<ts>_<site_name>_fill/ with subdirs."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    log_dir = _BASE_LOGS / f"{ts}_{site_name}_fill"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "dom_snapshots").mkdir(exist_ok=True)
    (log_dir / "llm_calls").mkdir(exist_ok=True)
    return log_dir


async def _detect_form_selector(session: BrowserSession) -> str:
    """Find the form with most inputs and return its CSS selector."""
    selector: str = await session.page.evaluate("""
    () => {
        const forms = document.querySelectorAll("form");
        if (!forms.length) return "form";
        let best = null, bestCount = 0;
        for (const f of forms) {
            const n = f.querySelectorAll("input, select, textarea").length;
            if (n > bestCount) { bestCount = n; best = f; }
        }
        if (!best) return "form";
        return best.id ? "#" + best.id : "form";
    }
    """)
    return selector or "form"


# ── Public API ───────────────────────────────────────────────────────────────

@dataclass
class FillExperimentReport:
    site_url: str
    target_value: str
    success: bool
    location_exploration: LocationExplorationReport
    fill_result: FillResult | None
    state_diff: FormStateDiff | None
    duration_seconds: float
    cost_estimate_eur: float
    error: str | None


async def fill_location_experiment(
    url: str,
    target_value: str,
    log_dir: Path,
    headless: bool = True,
) -> FillExperimentReport:
    """
    Full flow:
    1. Navigate to url.
    2. Explore: close cookies → find form → identify fields → classify location widget.
    3. Detect form selector.
    4. Capture form state BEFORE.
    5. Get filler for widget type + fill.
    6. Capture form state AFTER.
    7. Diff before/after → verify fill worked.
    """
    t0 = time.monotonic()
    total_cost = 0.0
    client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    def _fail(
        msg: str,
        exploration: LocationExplorationReport | None = None,
    ) -> FillExperimentReport:
        _log(log_dir, "result", success=False, error=msg)
        return FillExperimentReport(
            site_url=url,
            target_value=target_value,
            success=False,
            location_exploration=exploration,
            fill_result=None,
            state_diff=None,
            duration_seconds=time.monotonic() - t0,
            cost_estimate_eur=total_cost,
            error=msg,
        )

    async with BrowserSession(headless=headless) as session:
        # 1. Navigate
        _log(log_dir, "navigate", url=url)
        nav_t0 = time.monotonic()
        await session.navigate(url)
        _log(log_dir, "navigate_complete",
             url=url, duration_ms=int((time.monotonic() - nav_t0) * 1000))

        # 2. Explore (reuse existing pipeline)
        exploration = await explore_location_field_in_session(
            session=session,
            site_name="",   # not used in sub-steps; for the report only
            url=url,
            log_dir=log_dir,
            client=client,
        )
        total_cost += exploration.cost_estimate_eur

        if not exploration.success:
            return _fail(
                f"Exploration failed: {exploration.error}",
                exploration=exploration,
            )

        field = exploration.all_fields.pickup_location
        widget = exploration.location_widget

        # 3. Choose filler
        try:
            filler = get_filler_for_widget(widget)
        except UnsupportedWidgetError as exc:
            return _fail(str(exc), exploration=exploration)

        _log(log_dir, "filler_selected", strategy=filler.strategy_name,
             widget_type=widget.widget_type)

        # 4. Detect form selector + capture state BEFORE
        form_selector = await _detect_form_selector(session)
        _log(log_dir, "form_selector_detected", selector=form_selector)

        state_before = await capture_form_state(session, form_selector)
        _log(log_dir, "form_state_captured_before",
             input_count=len(state_before.inputs))
        save_state_snapshot(log_dir, "05_form_state_before.json", state_before)

        # 5. Fill
        logger = SessionLogger(log_dir)   # appends to existing trace.jsonl
        fill_result = await filler.fill(
            session=session,
            field=field,
            widget=widget,
            target_value=target_value,
            form_selector=form_selector,
            logger=logger,
        )

        if not fill_result.success:
            return FillExperimentReport(
                site_url=url,
                target_value=target_value,
                success=False,
                location_exploration=exploration,
                fill_result=fill_result,
                state_diff=None,
                duration_seconds=time.monotonic() - t0,
                cost_estimate_eur=total_cost,
                error=f"Fill failed: {fill_result.error}",
            )

        # 6. Capture state AFTER
        state_after = await capture_form_state(session, form_selector)
        _log(log_dir, "form_state_captured_after",
             input_count=len(state_after.inputs))
        save_state_snapshot(log_dir, "07_form_state_after.json", state_after)

        # 7. Diff
        diff = compare_states(state_before, state_after)
        _log(log_dir, "form_state_diff",
             has_changes=diff.has_changes,
             changed_count=len(diff.changed_inputs),
             added_count=len(diff.added_inputs))
        save_state_snapshot(log_dir, "08_state_diff.json", diff)

        success = fill_result.success and diff.has_changes
        _log(log_dir, "result",
             success=success,
             matched_option=fill_result.matched_option,
             has_changes=diff.has_changes,
             cost_eur=total_cost)

        return FillExperimentReport(
            site_url=url,
            target_value=target_value,
            success=success,
            location_exploration=exploration,
            fill_result=fill_result,
            state_diff=diff,
            duration_seconds=time.monotonic() - t0,
            cost_estimate_eur=total_cost,
            error=None if success else "Fill succeeded but no state change detected",
        )
