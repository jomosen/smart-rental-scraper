"""CLI runner for Experiment 7: fill form → submit → detect results."""
from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from location_explorer import create_log_dir
from submit_runner import SubmitExperimentReport, submit_and_detect

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
    p = argparse.ArgumentParser(description="Submit form + detect results page (Experiment 7)")
    p.add_argument("--visible", action="store_true", help="Show browser window")
    p.add_argument("--location", default="Alicante", help="Pickup location (default: Alicante)")
    p.add_argument("--pickup-offset", type=int, default=48, metavar="DAYS",
                   help="Days from today for pickup date (default: 48)")
    p.add_argument("--pickup-time", default="10:00", help="Pickup time (default: 10:00)")
    p.add_argument("--dropoff-time", default="10:00", help="Dropoff time (default: 10:00)")
    return p.parse_args()


def print_report(report: SubmitExperimentReport) -> None:
    overall = "[OK]" if report.success else "[FAIL]"
    print(f"  {overall}  duration={report.duration_seconds:.1f}s  "
          f"cost={report.cost_estimate_eur:.4f} EUR  llm_calls={report.llm_calls}")

    form_status = "[OK]" if report.form_filled else "[FAIL]"
    print(f"  form_filled:    {form_status}")

    submit_status = "[OK]" if report.submit_clicked else "[FAIL]"
    print(f"  submit_clicked: {submit_status}")

    if report.wait_outcome is not None:
        w = report.wait_outcome
        ready_flag = "[OK]" if w.ready else "[FAIL]"
        print(f"  wait:           {ready_flag}  signal={w.signal}  "
              f"candidates={w.candidate_count}  waited={w.waited_ms}ms  "
              f"url_changed={w.url_before != w.url_after}")

    if report.confirmation is not None:
        c = report.confirmation
        vehicle_str = (
            f"  vehicles~{c.approx_vehicle_count}" if c.approx_vehicle_count is not None else ""
        )
        print(f"  page_type:      {c.page_type}{vehicle_str}")
        print(f"  rationale:      {c.rationale}")
        if c.error:
            print(f"  confirm_error:  {c.error}")

    if report.error:
        print(f"  error:          {report.error}")


async def main() -> None:
    args = parse_args()
    targets = compute_targets(args)

    for name, url in TEST_CASES:
        log_dir = create_log_dir(name, suffix="_submit")
        print(f"\n=== {name} -> {targets} ===")
        print(f"  Logs: {log_dir}")
        try:
            report = await submit_and_detect(
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
