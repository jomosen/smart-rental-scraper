"""Heuristic cookie-banner candidate extractor using BeautifulSoup."""
from __future__ import annotations

from bs4 import BeautifulSoup, Tag

_KEYWORD_ATTRS = {
    "cookie", "cookies", "consent", "gdpr", "rgpd", "banner",
    "notice", "privacy", "privacidad", "consentimiento", "privacité",
    "datenschutz", "overlay", "modal", "popup",
}

# Multilingual consent keywords for visible text scoring.
_CONSENT_TEXT_KW = (
    "cookie", "cookies", "aceptar", "accept", "consent", "consentimiento",
    "privacy", "privacidad", "gdpr", "rgpd", "datenschutz",
)

_MAX_CHARS = 2_000


def _score_element(tag: Tag) -> int:
    score = 0

    # Structural modal/dialog signals — strongest indicators of a banner overlay.
    if tag.name == "dialog":
        score += 8
    if tag.get("aria-modal") == "true":
        score += 7

    # ARIA roles — dialog roles boosted; banner role (page <header> landmark) kept low.
    role = tag.get("role", "")
    if role in ("dialog", "alertdialog"):
        score += 6
    elif role == "banner":
        score += 1   # WAI-ARIA landmark for <header> — very common false positive

    # Keyword matches in common attributes.
    for attr in ("id", "class", "aria-label", "data-testid"):
        value = tag.get(attr, "")
        if isinstance(value, list):
            value = " ".join(value)
        if any(kw in value.lower() for kw in _KEYWORD_ATTRS):
            score += 3

    # Consent-related visible text (multilingual).
    text = tag.get_text(" ", strip=True).lower()
    if any(kw in text for kw in _CONSENT_TEXT_KW):
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
