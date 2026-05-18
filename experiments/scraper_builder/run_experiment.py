"""
Entry point for the cookie-banner closer experiment.

Usage:
    cd experiments/scraper_builder
    python run_experiment.py [--visible]
"""
from __future__ import annotations

import asyncio
import os
import sys
from argparse import ArgumentParser
from dataclasses import asdict

from dotenv import load_dotenv

# Allow sibling imports without installing as a package
sys.path.insert(0, os.path.dirname(__file__))

from browser_session import BrowserSession
from cookie_closer import close_cookies
from models import CloseResult

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
        async with BrowserSession(headless=headless) as session:
            result = await close_cookies(session, url)
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
