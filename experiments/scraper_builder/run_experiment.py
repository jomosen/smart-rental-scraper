"""
Entry point for the cookie-banner closer experiment.

Usage:
    cd experiments/scraper_builder
    python run_experiment.py [--visible]
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

from src.scraper.infrastructure.builder.cookie_closer import close_cookies
from src.scraper.infrastructure.builder.models import CloseResult

load_dotenv()

TEST_SITES: list[tuple[str, str]] = [
    ("centauro", "https://www.centauro.net"),
]


def _fmt(result: CloseResult) -> str:
    lines = [
        f"  action           : {result.action}",
        f"  success          : {result.success}",
        f"  selector         : {result.selector}",
        f"  selector_type    : {result.selector_type}",
        f"  rationale        : {result.rationale}",
        f"  attempts         : {result.attempts}",
        f"  cost_estimate_eur: {result.cost_estimate_eur:.6f}",
        f"  duration_seconds : {result.duration_seconds:.2f}",
    ]
    if result.error:
        lines.append(f"  error            : {result.error}")
    return "\n".join(lines)


async def run(headless: bool) -> None:
    for name, url in TEST_SITES:
        print(f"\n{'='*60}")
        print(f"Site: {name}  ({url})")
        print("="*60)
        result = await close_cookies(url, site_name=name, headless=headless)
        if result.log_dir:
            print(f"  Logs: {result.log_dir}")
        print(_fmt(result))


def main() -> None:
    parser = ArgumentParser(description="Cookie banner closer experiment")
    parser.add_argument(
        "--visible",
        action="store_true",
        help="Run browser in non-headless mode (shows the window)",
    )
    args = parser.parse_args()
    asyncio.run(run(headless=not args.visible))


if __name__ == "__main__":
    main()
