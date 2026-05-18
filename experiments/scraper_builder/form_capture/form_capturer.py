"""Grab the search form from the currently loaded page and persist snapshots."""
from __future__ import annotations

from pathlib import Path

from browser_session import BrowserSession
from .form_locator import locate_search_form_raw
from .html_cleaner import clean_html_for_llm


async def capture_search_form(session: BrowserSession, log_dir: Path) -> tuple[str, str] | None:
    """
    Capture the search form from the page currently loaded in *session*.

    Assumes the page is already loaded and any cookie banner has been closed.

    Saves:
    - dom_snapshots/01_initial_full.html — raw full-page HTML
    - dom_snapshots/02_form_raw.html     — raw form candidate
    - dom_snapshots/03_form_cleaned.html — cleaned form passed to the LLM

    Returns (raw_form_html, cleaned_form_html), or None if no form is found.
    """
    snaps = log_dir / "dom_snapshots"
    snaps.mkdir(exist_ok=True)

    full_html = await session.get_html()
    (snaps / "01_initial_full.html").write_text(full_html, encoding="utf-8")

    raw_form = locate_search_form_raw(full_html)
    if raw_form is None:
        return None

    cleaned_form = clean_html_for_llm(raw_form)
    (snaps / "02_form_raw.html").write_text(raw_form, encoding="utf-8")
    (snaps / "03_form_cleaned.html").write_text(cleaned_form, encoding="utf-8")

    return raw_form, cleaned_form
