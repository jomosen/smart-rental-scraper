"""Heuristic: find the booking-search form in a full page HTML string."""
from __future__ import annotations

from bs4 import BeautifulSoup, Tag

from .html_cleaner import clean_html_for_llm

SEARCH_FORM_KEYWORDS = [
    "pickup", "recoger", "recogida", "origen", "origin",
    "fecha", "date", "search", "buscar", "reservar",
    "alquiler", "rental",
]

_MAX_RAW_BYTES = 60_000  # discard elements bigger than this (likely the whole page body)


def _score(tag: Tag) -> int:
    markup = str(tag).lower()
    return sum(kw in markup for kw in SEARCH_FORM_KEYWORDS)


def _input_count(tag: Tag) -> int:
    return len(tag.find_all(["input", "select", "textarea", "button"]))


def _find_best(soup: BeautifulSoup, tag_names: list[str], min_score: int, min_inputs: int) -> Tag | None:
    candidates: list[tuple[int, Tag]] = []
    for tag in soup.find_all(tag_names):
        raw = str(tag)
        if len(raw.encode()) > _MAX_RAW_BYTES:
            continue
        score = _score(tag)
        inputs = _input_count(tag)
        if score >= min_score and inputs >= min_inputs:
            candidates.append((score * 10 + inputs, tag))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def locate_search_form_raw(html: str) -> str | None:
    """Return the raw (uncleaned) HTML of the best search-form candidate, or None."""
    soup = BeautifulSoup(html, "html.parser")

    # Strategy 1: semantic <form> elements
    tag = _find_best(soup, ["form"], min_score=1, min_inputs=2)
    if tag is not None:
        return str(tag)

    # Strategy 2: structural containers with keywords + inputs
    tag = _find_best(soup, ["div", "section", "article", "aside"], min_score=2, min_inputs=2)
    if tag is not None:
        return str(tag)

    return None


def locate_search_form(html: str) -> str | None:
    """Return the cleaned HTML of the best search-form candidate, or None."""
    raw = locate_search_form_raw(html)
    return clean_html_for_llm(raw) if raw is not None else None
