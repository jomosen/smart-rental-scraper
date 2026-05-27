"""CLI runner for Experiment 6: fill the entire search form end-to-end."""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

_PROJECT_ROOT = Path(__file__).parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.scraper.infrastructure.builder.form_fill_orchestrator import (
    FieldFillOutcome,
    FormFillReport,
    fill_form,
)
from src.scraper.infrastructure.builder.location_explorer import create_log_dir

TEST_CASES = [
    ("centauro", "https://www.centauro.net"),
]


def compute_targets(args: argparse.Namespace) -> dict:
    pickup_d = date.today() + timedelta(days=args.pickup_offset)
    return_d = pickup_d + timedelta(days=7)
    return {
        "location": args.location,
        "pickup_date": pickup_d,
        "return_date": return_d,
        "pickup_time": args.pickup_time,
        "return_time": args.dropoff_time,
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fill entire search form (Experiment 6)")
    p.add_argument("--visible", action="store_true", help="Show browser window")
    p.add_argument("--location", default="Alicante", help="Pickup location (default: Alicante)")
    p.add_argument("--pickup-offset", type=int, default=48, metavar="DAYS",
                   help="Days from today for pickup date (default: 48)")
    p.add_argument("--pickup-time", default="10:00", help="Pickup time (default: 10:00)")
    p.add_argument("--dropoff-time", default="10:00", help="Dropoff time (default: 10:00)")
    return p.parse_args()


def _outcome_line(o: FieldFillOutcome) -> str:
    if not o.attempted:
        return f"  {o.field_name:<20}  SKIP   target={o.target!r}"
    status = "[OK]  " if o.success else "[FAIL]"
    strategy = o.strategy or "-"
    line = f"  {o.field_name:<20}  {status}  strategy={strategy:<30}  target={o.target!r}"
    if o.error:
        line += f"\n    Error: {o.error}"
    return line


def print_report(report: FormFillReport) -> None:
    overall = "[OK]" if report.success else "[FAIL]"
    print(f"  {overall}  duration={report.duration_seconds:.1f}s  "
          f"cost={report.cost_estimate_eur:.4f} EUR  llm_calls={report.llm_calls}")
    for outcome in report.outcomes:
        print(_outcome_line(outcome))
    if report.failed_at:
        print(f"  Stopped at: {report.failed_at}")
    if report.error:
        print(f"  Error: {report.error}")


async def main() -> None:
    args = parse_args()
    targets = compute_targets(args)

    for name, url in TEST_CASES:
        log_dir = create_log_dir(name, suffix="_form")
        print(f"\n=== {name} -> {targets} ===")
        print(f"  Logs: {log_dir}")
        try:
            report = await fill_form(
                url=url,
                targets=targets,
                log_dir=log_dir,
                headless=not args.visible,
            )
            print_report(report)
        except Exception as exc:
            print(f"  EXCEPTION: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
