"""Audit logger for a single close_cookies() execution."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

_BASE_DIR = Path(__file__).parent / "logs"


class SessionLogger:
    def __init__(self, name_or_dir: "str | Path", base_dir: Path = _BASE_DIR) -> None:
        if isinstance(name_or_dir, Path):
            # Reuse an existing log directory created by the caller.
            self.log_dir = name_or_dir
        else:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
            self.log_dir = base_dir / f"{ts}_{name_or_dir}"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        (self.log_dir / "dom_snapshots").mkdir(exist_ok=True)
        (self.log_dir / "llm_calls").mkdir(exist_ok=True)
        self._trace_path = self.log_dir / "trace.jsonl"
        self._snapshot_counter = 0
        self._call_counter = 0
        self._event_counter = 0

    # ── Public helpers ───────────────────────────────────────────────────────────

    def now(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def log(self, type: str, **kwargs) -> None:
        event = {"ts": self.now(), "type": type, **kwargs}
        with open(self._trace_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._event_counter += 1

    def save_snapshot(self, html: str, label: str) -> str:
        """Persist html to dom_snapshots/NNN_label.html; return the filename."""
        self._snapshot_counter += 1
        fname = f"{self._snapshot_counter:03d}_{label}.html"
        (self.log_dir / "dom_snapshots" / fname).write_text(html, encoding="utf-8")
        return fname

    def save_llm_call(self, candidates: list[dict], decision, cost_eur: float) -> str:
        """Save LLM input/output; return the zero-padded call_id string."""
        self._call_counter += 1
        call_id = f"{self._call_counter:03d}"
        input_html = "\n\n".join(
            f"<!-- Candidate {i} tag={c['tag']} score={c['score']} -->\n{c['html']}"
            for i, c in enumerate(candidates, 1)
        )
        (self.log_dir / "llm_calls" / f"call_{call_id}_input.html").write_text(
            input_html, encoding="utf-8"
        )
        output = {
            "selector": decision.selector,
            "selector_type": decision.selector_type,
            "rationale": decision.rationale,
            "tokens_input": decision.tokens_input,
            "tokens_output": decision.tokens_output,
            "cost_eur": cost_eur,
        }
        (self.log_dir / "llm_calls" / f"call_{call_id}_output.json").write_text(
            json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return call_id

    def write_summary(self, result, site_name: str, url: str, started_at: str) -> None:
        summary = {
            "site_name": site_name,
            "url": url,
            "started_at": started_at,
            "ended_at": self.now(),
            "duration_seconds": result.duration_seconds,
            "result": {
                "success": result.success,
                "action": result.action,
                "selector": result.selector,
                "attempts": result.attempts,
            },
            "llm_calls_count": self._call_counter,
            "total_cost_eur": result.cost_estimate_eur,
            "trace_events": self._event_counter,
        }
        (self.log_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
