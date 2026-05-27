"""CLI runner for Experiment 5: fill pickup_time + dropoff_time."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

_PROJECT_ROOT = Path(__file__).parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.scraper.infrastructure.builder.time_filler_runner import (
    TimeFillExperimentReport,
    fill_times_experiment,
)
from src.scraper.infrastructure.builder.location_explorer import create_log_dir


PICKUP_TIME = "10:00"
DROPOFF_TIME = "10:00"

TEST_CASES = [
    ("centauro", "https://www.centauro.net"),
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fill pickup + dropoff times (Experiment 5)")
    p.add_argument("--visible", action="store_true", help="Show browser window")
    p.add_argument("--pickup-time", default=None, metavar="HH:MM",
                   help=f"Pickup time (default: {PICKUP_TIME})")
    p.add_argument("--dropoff-time", default=None, metavar="HH:MM",
                   help=f"Dropoff time (default: {DROPOFF_TIME})")
    return p.parse_args()


def print_report(report: TimeFillExperimentReport) -> None:
    status = "[OK]" if report.success else "[FAIL]"
    print(f"  {status}  duration={report.duration_seconds:.1f}s  "
          f"cost={report.cost_estimate_eur:.4f} EUR")

    if report.pickup_result:
        p_ok = report.pickup_result.success
        print(f"  pickup:   success={p_ok}  "
              f"strategy={report.pickup_result.strategy_used}  "
              f"matched={report.pickup_result.matched_option!r}")

    if report.dropoff_result:
        d_ok = report.dropoff_result.success
        print(f"  dropoff:  success={d_ok}  "
              f"strategy={report.dropoff_result.strategy_used}  "
              f"matched={report.dropoff_result.matched_option!r}")

    if report.error:
        print(f"  Error: {report.error}")


async def main() -> None:
    args = parse_args()
    pickup_t = args.pickup_time or PICKUP_TIME
    dropoff_t = args.dropoff_time or DROPOFF_TIME

    for name, url in TEST_CASES:
        log_dir = create_log_dir(name, suffix="_times")
        print(f"\n=== {name} -> pickup={pickup_t} dropoff={dropoff_t} ===")
        print(f"  Logs: {log_dir}")
        try:
            report = await fill_times_experiment(
                url=url,
                pickup_time=pickup_t,
                dropoff_time=dropoff_t,
                log_dir=log_dir,
                headless=not args.visible,
            )
            print_report(report)
        except Exception as exc:
            print(f"  EXCEPTION: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
