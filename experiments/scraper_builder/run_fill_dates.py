"""CLI runner for Experiment 4: fill pickup_date + return_date."""
from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta
from pathlib import Path

from date_filler_runner import DateFillExperimentReport, fill_dates_experiment
from location_explorer import create_log_dir


TEST_CASES = [
    ("centauro", "https://www.centauro.net"),
]


def compute_targets(pickup_offset: int = 48) -> tuple[date, date]:
    pickup = date.today() + timedelta(days=pickup_offset)
    return_ = pickup + timedelta(days=7)
    return pickup, return_


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fill pickup + return dates (Experiment 4)")
    p.add_argument("--visible", action="store_true", help="Show browser window")
    p.add_argument(
        "--pickup-offset",
        type=int,
        default=48,
        metavar="DAYS",
        help="Days from today for pickup date (default: 48)",
    )
    return p.parse_args()


def print_report(report: DateFillExperimentReport) -> None:
    status = "[OK]" if report.success else "[FAIL]"
    print(f"  {status}  duration={report.duration_seconds:.1f}s  cost={report.cost_estimate_eur:.4f} EUR")

    if report.pickup_result:
        p_ok = report.pickup_result.success
        p_chg = report.pickup_diff.has_changes if report.pickup_diff else False
        print(f"  pickup:  success={p_ok}  has_changes={p_chg}  "
              f"strategy={report.pickup_result.strategy_used}  "
              f"value={report.pickup_result.target_value!r}")
        if report.pickup_diff and report.pickup_diff.changed_inputs:
            for k, (before, after) in report.pickup_diff.changed_inputs.items():
                print(f"    {k}: {before!r} -> {after!r}")

    if report.return_result:
        r_ok = report.return_result.success
        r_chg = report.return_diff.has_changes if report.return_diff else False
        print(f"  return:  success={r_ok}  has_changes={r_chg}  "
              f"strategy={report.return_result.strategy_used}  "
              f"value={report.return_result.target_value!r}")
        if report.return_diff and report.return_diff.changed_inputs:
            for k, (before, after) in report.return_diff.changed_inputs.items():
                print(f"    {k}: {before!r} -> {after!r}")

    if report.error:
        print(f"  Error: {report.error}")


async def main() -> None:
    args = parse_args()
    pickup_target, return_target = compute_targets(args.pickup_offset)

    for name, url in TEST_CASES:
        log_dir = create_log_dir(name, suffix="_dates")
        print(f"\n=== {name} -> pickup={pickup_target}  return={return_target} ===")
        print(f"  Logs: {log_dir}")
        try:
            report = await fill_dates_experiment(
                url=url,
                pickup_target=pickup_target,
                return_target=return_target,
                log_dir=log_dir,
                headless=not args.visible,
            )
            print_report(report)
        except Exception as exc:
            print(f"  EXCEPTION: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
