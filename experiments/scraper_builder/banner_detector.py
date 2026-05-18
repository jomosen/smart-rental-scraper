"""Heuristic cookie-banner candidate extractor using BeautifulSoup."""
from __future__ import annotations

from bs4 import BeautifulSoup, Tag

_KEYWORD_ATTRS = {
    "cookie", "cookies", "consent", "gdpr", "banner",
    "notice", "privacy", "overlay", "modal", "popup",
}

_MAX_CHARS = 2_000


def _score_element(tag: Tag) -> int:
    score = 0
    for attr in ("id", "class", "aria-label", "data-testid"):
        value = tag.get(attr, "")
        if isinstance(value, list):
            value = " ".join(value)
        value_lower = value.lower()
        if any(kw in value_lower for kw in _KEYWORD_ATTRS):
            score += 3

    if tag.get("role") in ("dialog", "alertdialog", "banner"):
        score += 2

    text = tag.get_text(" ", strip=True)
    if any(kw in text.lower() for kw in ("cookie", "cookies", "aceptar", "accept", "consent")):
        score += 2

    return score


def extract_banner_candidates(html: str, top_n: int = 3) -> list[dict]:
    """
    Return up to top_n serialised candidate elements most likely to be a
    cookie banner, each as {"html": str, "tag": str, "score": int}.
    Elements whose serialised HTML exceeds _MAX_CHARS are truncated.
    """
    soup = BeautifulSoup(html, "html.parser")
    scored: list[tuple[int, Tag]] = []

    for tag in soup.find_all(True):
        if tag.name in ("html", "head", "body", "script", "style", "noscript"):
            continue
        score = _score_element(tag)
        if score > 0:
            scored.append((score, tag))

    scored.sort(key=lambda x: x[0], reverse=True)

    results = []
    seen_html: set[str] = set()
    for score, tag in scored:
        raw = str(tag)
        if raw in seen_html:
            continue
        seen_html.add(raw)
        results.append({
            "html": raw[:_MAX_CHARS],
            "tag": tag.name,
            "score": score,
        })
        if len(results) >= top_n:
            break

    return results
