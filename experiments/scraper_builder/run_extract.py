"""CLI runner for Fase A1: form fill → submit → extract → verify."""
from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta

from dotenv import load_dotenv
load_dotenv()

from extraction_runner import ExtractionExperimentReport, extract_experiment
from location_explorer import create_log_dir

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
    p = argparse.ArgumentParser(description="Extract + verify vehicle results (Fase A1)")
    p.add_argument("--visible", action="store_true", help="Show browser window")
    p.add_argument("--location", default="Alicante", help="Pickup location (default: Alicante)")
    p.add_argument("--pickup-offset", type=int, default=48, metavar="DAYS",
                   help="Days from today for pickup date (default: 48)")
    p.add_argument("--pickup-time", default="10:00", help="Pickup time (default: 10:00)")
    p.add_argument("--dropoff-time", default="10:00", help="Dropoff time (default: 10:00)")
    return p.parse_args()


def print_report(report: ExtractionExperimentReport) -> None:
    overall = "[OK]" if report.success else "[FAIL]"
    print(f"  {overall}  duration={report.duration_seconds:.1f}s  "
          f"cost={report.cost_estimate_eur:.4f} EUR  llm_calls={report.llm_calls}")

    reached_flag = "[OK]" if report.reached_results else "[FAIL]"
    print(f"  reached_results: {reached_flag}")

    llm_n = len(report.llm_vehicles)
    dom_n = len(report.dom_vehicles)
    print(f"  vehicles:        LLM={llm_n}  DOM={dom_n}")

    print(f"  scroll:          rounds={report.scroll_rounds}  "
          f"final_count={report.scroll_final_count}")

    if report.structure:
        print(f"  card_selector:   {report.structure.vehicle_card_selector!r}")
        print(f"  price_strategy:  {report.structure.price_strategy}")

    if report.verification:
        v = report.verification
        match_flag = "[OK]" if v.match else "[FAIL]"
        print(f"  verification:    {match_flag}  {v.rationale}")
        # Field agreement table (only fields with any comparison)
        compared_fields = {
            f: pct for f, pct in v.field_agreement.items()
            if pct < 1.0 or f in ("model", "group_code", "price_final")
        }
        if compared_fields:
            print("  field_agreement:")
            for field, pct in compared_fields.items():
                bar = "[OK]" if pct >= 0.90 else "[LOW]"
                print(f"    {field:<22}  {bar}  {pct:.0%}")
        if v.mismatches:
            print(f"  mismatches ({len(v.mismatches)}):")
            for m in v.mismatches[:8]:
                print(f"    - {m}")
            if len(v.mismatches) > 8:
                print(f"    ... and {len(v.mismatches) - 8} more")

    # Sample vehicles from LLM extraction
    sample = report.llm_vehicles[:3]
    if sample:
        print("  sample (LLM, first 3):")
        for v in sample:
            price_str = f"{v.price_final} {v.currency}" if v.price_final else "?"
            seats_str = f"  {v.seats}p" if v.seats else ""
            print(f"    {v.model or '?':<25}  {(v.group_code or '?'):<8}  "
                  f"{(v.transmission or '?'):<3}  {price_str}{seats_str}")

    if report.error:
        print(f"  error:           {report.error}")


async def main() -> None:
    args = parse_args()
    targets = compute_targets(args)

    for name, url in TEST_CASES:
        log_dir = create_log_dir(name, suffix="_extract")
        print(f"\n=== {name} -> {targets} ===")
        print(f"  Logs: {log_dir}")
        try:
            report = await extract_experiment(
                url=url,
                targets=targets,
                log_dir=log_dir,
                headless=not args.visible,
            )
            print_report(report)
        except Exception as exc:
            print(f"  EXCEPTION: {exc}")
            raise


if __name__ == "__main__":
    asyncio.run(main())
