"""Strip noise from HTML before passing it to an LLM."""
from __future__ import annotations

from bs4 import BeautifulSoup, Comment

_REMOVE_TAGS = frozenset({
    "script", "style", "noscript", "template",
    "link", "meta", "head", "svg", "img",
})

_KEEP_ATTRS = frozenset({
    "id", "class", "name", "role", "type", "placeholder",
    "value", "for", "href", "action", "method",
    "aria-label", "aria-labelledby", "aria-placeholder",
    "aria-expanded", "aria-haspopup", "aria-controls",
    "data-testid", "data-cy", "data-action", "data-field",
    "data-name", "data-id", "data-value", "data-type",
    "data-placeholder", "data-label",
})


def clean_html_for_llm(html: str) -> str:
    """Remove noise that wastes LLM context without helping it understand the form."""
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(_REMOVE_TAGS):
        tag.decompose()

    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()

    for tag in soup.find_all(True, attrs={"hidden": True}):
        tag.decompose()

    for tag in soup.find_all(True, style=True):
        if "display:none" in tag.get("style", "").replace(" ", "").lower():
            tag.decompose()

    for tag in soup.find_all(True):
        bad = [a for a in list(tag.attrs) if a not in _KEEP_ATTRS]
        for attr in bad:
            del tag.attrs[attr]

    return str(soup)
