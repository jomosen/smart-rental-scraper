"""Isolate the vehicle-list container from the live DOM for compact LLM input."""
from __future__ import annotations

from browser_session import BrowserSession

# Find the tightest common ancestor of all visible price-bearing elements.
# Uses the same currency-regex TreeWalker as results_waiter — no class names.
_CONTAINER_JS = """
() => {
    const priceRe = /[\\u20ac$\\u00a3]\\s*\\d+[\\d.,]*|\\d[\\d.,]*\\s*[\\u20ac$\\u00a3]|\\d[\\d.,]*\\s*(EUR|USD|GBP)/i;

    // Collect visible price-bearing elements from text nodes and aria-labels
    const priceEls = new Set();
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
        if (priceRe.test(node.textContent)) {
            const p = node.parentElement;
            if (p && p.offsetParent !== null) priceEls.add(p);
        }
    }
    for (const el of document.querySelectorAll('[aria-label]')) {
        if (priceRe.test(el.getAttribute('aria-label') || '') &&
                el.offsetParent !== null) {
            priceEls.add(el);
        }
    }

    const elements = [...priceEls];
    if (elements.length === 0) return null;

    // Find common ancestor: walk up from elements[0] until it contains all others
    let ancestor = elements[0];
    for (const el of elements.slice(1)) {
        while (ancestor && !ancestor.contains(el)) {
            ancestor = ancestor.parentElement;
        }
        if (!ancestor || ancestor === document.documentElement) {
            return document.body.outerHTML;
        }
    }

    // Tighten: descend into the single child that still contains ALL elements
    let changed = true;
    while (changed) {
        changed = false;
        for (const child of ancestor.children) {
            if (elements.every(el => child === el || child.contains(el))) {
                ancestor = child;
                changed = true;
                break;
            }
        }
    }

    return ancestor.outerHTML;
}
"""


async def extract_results_container_html(session: BrowserSession) -> str | None:
    """
    Return the outerHTML of the tightest DOM element that contains all
    visible price-bearing elements (the vehicle-list container).

    Uses a currency-regex TreeWalker — no provider-specific selectors or class names.
    Returns None if no price elements are found on the page.
    """
    try:
        result: str | None = await session.page.evaluate(_CONTAINER_JS)
        return result or None
    except Exception:
        return None
