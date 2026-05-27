"""CLI runner for Experiment 3: fill the location field."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.scraper.infrastructure.builder.location_filler_runner import (
    FillExperimentReport,
    create_fill_log_dir,
    fill_location_experiment,
)

load_dotenv()

TEST_CASES = [
    ("centauro", "https://www.centauro.net", "Alicante"),
]


def _print_report(report: FillExperimentReport) -> None:
    status = "OK" if report.success else "FAIL"
    print(f"  [{status}] duration={report.duration_seconds:.1f}s  cost=€{report.cost_estimate_eur:.4f}")

    expl = report.location_exploration
    if expl:
        print(f"  Exploration: widget={expl.location_widget.widget_type if expl.location_widget else 'n/a'}")
        if expl.all_fields and expl.all_fields.pickup_location:
            pf = expl.all_fields.pickup_location
            print(f"  Field: {pf.selector!r} ({pf.element_kind})")

    if report.fill_result:
        fr = report.fill_result
        print(f"  Filler: strategy={fr.strategy_used}  matched={fr.matched_option!r}")

    if report.state_diff:
        diff = report.state_diff
        print(f"  State diff: has_changes={diff.has_changes}  changed={len(diff.changed_inputs)}  added={len(diff.added_inputs)}")
        for key, (before, after) in diff.changed_inputs.items():
            before_display = f"{before!r:.40}" if before else "''"
            after_display = f"{after!r:.40}" if after else "''"
            print(f"    {key}: {before_display} -> {after_display}")

    if report.error:
        print(f"  Error: {report.error}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Fill location field experiment")
    parser.add_argument("--target", help="Override target location value for all sites")
    parser.add_argument("--visible", action="store_true", help="Run browser in visible mode")
    args = parser.parse_args()

    headless = not args.visible

    for name, url, default_target in TEST_CASES:
        target = args.target or default_target
        log_dir = create_fill_log_dir(name)
        print(f"\n=== {name} -> target: {target!r} ===")
        print(f"  Logs: {log_dir}")

        try:
            report = await fill_location_experiment(
                url=url,
                target_value=target,
                log_dir=log_dir,
                headless=headless,
            )
            _print_report(report)
        except Exception as exc:
            print(f"  EXCEPTION: {exc}")
            raise


if __name__ == "__main__":
    asyncio.run(main())
