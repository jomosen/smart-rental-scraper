"""
Entry point for location-field explorer (experiment 2).

Usage:
    cd experiments/scraper_builder
    python run_explore_location.py [--visible]
"""
from __future__ import annotations

import asyncio
import sys
from argparse import ArgumentParser
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.scraper.infrastructure.builder.location_explorer import (
    LocationExplorationReport,
    create_log_dir,
    explore_location_field,
)

load_dotenv()

TEST_SITES: list[tuple[str, str]] = [
    ("centauro", "https://www.centauro.net"),
]


def _fmt(r: LocationExplorationReport) -> str:
    lines = [
        f"  success          : {r.success}",
        f"  form_html_size   : {r.form_html_size}",
        f"  form_cleaned_size: {r.form_html_cleaned_size}",
    ]
    if r.all_fields and r.all_fields.pickup_location:
        f = r.all_fields.pickup_location
        lines += [
            "  pickup_location  :",
            f"    selector       : {f.selector}",
            f"    element_kind   : {f.element_kind}",
            f"    rationale      : {f.rationale}",
        ]
    else:
        lines.append("  pickup_location  : not identified")

    if r.location_widget:
        w = r.location_widget
        lines += [
            f"  widget_type      : {w.widget_type}",
            f"  options_container: {w.options_container_selector}",
            f"  option_item      : {w.option_item_selector}",
            f"  is_searchable    : {w.is_searchable}",
            f"  widget_rationale : {w.rationale}",
        ]
    else:
        lines.append("  widget_type      : —")

    lines += [
        f"  duration_seconds : {r.duration_seconds:.2f}",
        f"  llm_calls        : {r.llm_calls}",
        f"  cost_estimate_eur: {r.cost_estimate_eur:.6f}",
    ]
    if r.error:
        lines.append(f"  error            : {r.error}")
    return "\n".join(lines)


async def run(headless: bool) -> None:
    for name, url in TEST_SITES:
        print(f"\n{'='*60}")
        print(f"Site: {name}  ({url})")
        print("="*60)
        log_dir = create_log_dir(name)
        print(f"  Logs: {log_dir}")
        try:
            report = await explore_location_field(name, url, log_dir, headless=headless)
        except Exception as exc:
            print(f"  EXCEPTION: {exc}")
            continue
        print(_fmt(report))


def main() -> None:
    parser = ArgumentParser(description="Location field explorer experiment")
    parser.add_argument(
        "--visible",
        action="store_true",
        help="Run browser in non-headless mode",
    )
    args = parser.parse_args()
    asyncio.run(run(headless=not args.visible))


if __name__ == "__main__":
    main()
