"""Vía 2 (step 2): apply discovered selectors deterministically via Playwright."""
from __future__ import annotations

import json
import re

from browser_session import BrowserSession
from extraction.models import FieldSelector, ResultsStructure, VehicleResult
from session_logger import SessionLogger

_PRICE_FIELDS = frozenset({"price_final"})
_INT_FIELDS = frozenset({"seats"})

# Passed to JS new RegExp() — literal currency chars, no backslash escaping needed
_PRICE_RE_STR = "[€$£]\\s*\\d+[\\d.,]*|\\d[\\d.,]*\\s*[€$£]|\\d[\\d.,]*\\s*(?:EUR|USD|GBP)"


# ── Price parser ──────────────────────────────────────────────────────────────

def parse_price(text: str) -> tuple[float | None, str | None]:
    """
    Extract (value, currency) from a price string. Handles:
      - European decimal format: "273,00 €", "1.234,56 €"
      - Currency prefix: "€273", "EUR 273"
      - Embedded in longer text: "FIAT 500: 273,00 €. Descuento: 15%."

    Returns (float_value, currency_code) or (None, None) if no price found.
    """
    if not text:
        return None, None

    currency: str | None = None
    if "€" in text or "EUR" in text:
        currency = "EUR"
    elif "USD" in text:
        currency = "USD"
    elif "£" in text or "GBP" in text:
        currency = "GBP"
    elif "$" in text:
        currency = "USD"

    # European: 1.234,56 or 273,00
    m = re.search(r"(\d{1,3}(?:\.\d{3})*),(\d{2})\b", text)
    if m:
        integer_part = m.group(1).replace(".", "")
        value = float(f"{integer_part}.{m.group(2)}")
        return value, currency

    # US decimal: 273.00
    m = re.search(r"(\d+)\.(\d{2})\b", text)
    if m:
        value = float(f"{m.group(1)}.{m.group(2)}")
        return value, currency

    # Plain thousands: 1.234 or 1,234
    m = re.search(r"(\d{1,3}(?:[.,]\d{3})+)\b", text)
    if m:
        value = float(re.sub(r"[.,]", "", m.group(1)))
        return value, currency

    # Bare integer
    m = re.search(r"\b(\d+)\b", text)
    if m:
        value = float(m.group(1))
        return value, currency

    return None, currency


# ── Card deduplication via JS ─────────────────────────────────────────────────

# Marks valid cards with data-scraper-card="N", returns stats dict.
# Receives [cardSelector, priceReStr] as argument.
_MARK_CARDS_JS = """
([cardSelector, priceReStr]) => {
    const allCards = Array.from(document.querySelectorAll(cardSelector));
    const priceRe = new RegExp(priceReStr, 'i');

    // Remove previous marks
    document.querySelectorAll('[data-scraper-card]').forEach(
        el => el.removeAttribute('data-scraper-card'));

    // Keep leaf cards: discard any element that CONTAINS another matched element.
    // This removes container/wrapper elements that accidentally match the selector.
    const leafCards = allCards.filter(el =>
        !allCards.some(other => other !== el && el.contains(other)));

    // Keep only complete cards: must have a price somewhere (text or aria-label).
    const complete = leafCards.filter(el => {
        if (priceRe.test(el.textContent)) return true;
        for (const d of el.querySelectorAll('[aria-label]')) {
            if (priceRe.test(d.getAttribute('aria-label') || '')) return true;
        }
        return false;
    });

    complete.forEach((el, i) => el.setAttribute('data-scraper-card', String(i)));
    return {total: allCards.length, afterDedup: leafCards.length, valid: complete.length};
}
"""

# JS price-extraction cascade. Called via card.evaluate(js, priceReStr).
# Returns {source, text} for aria-label, {source, nonStruck, struck} for text,
# or null if no price found.
_PRICE_CASCADE_JS = """
(el, priceReStr) => {
    const priceRe = new RegExp(priceReStr, 'i');

    // S1: aria-label on any descendant (highest fidelity — often has clean price)
    for (const d of el.querySelectorAll('[aria-label]')) {
        const label = d.getAttribute('aria-label') || '';
        if (priceRe.test(label)) {
            return {source: 'aria_label', text: label};
        }
    }

    // S2: text nodes, separating struck (price_original) from non-struck (price_final)
    const struckTags = new Set(['S', 'DEL', 'STRIKE']);

    function isStruck(textNode) {
        let p = textNode.parentElement;
        while (p && p !== el) {
            if (struckTags.has(p.tagName)) return true;
            if ((p.getAttribute('style') || '').includes('line-through')) return true;
            p = p.parentElement;
        }
        return false;
    }

    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    const nonStruck = [], struck = [];
    let node;
    while ((node = walker.nextNode())) {
        const t = node.textContent.trim();
        if (!priceRe.test(t)) continue;
        (isStruck(node) ? struck : nonStruck).push(t);
    }

    if (nonStruck.length || struck.length) {
        return {source: 'text', nonStruck: nonStruck, struck: struck};
    }
    return null;
}
"""

# JS: scan all descendants for aria-label/title containing seats keywords.
# Returns integer seat count or null.
_SEATS_BY_ARIA_JS = """
el => {
    const keywords = ['plazas', 'asientos', 'seats', 'places', 'sitzplätze', 'posti', 'plaza'];
    for (const d of el.querySelectorAll('[aria-label], [title]')) {
        const label = (d.getAttribute('aria-label') || d.getAttribute('title') || '').toLowerCase();
        if (keywords.some(kw => label.includes(kw))) {
            const m = label.match(/\\b(\\d+)\\b/);
            if (m) return parseInt(m[1], 10);
        }
    }
    return null;
}
"""

# JS: scan all descendants for aria-label/title indicating transmission type.
# Returns 'A' (automatic), 'M' (manual), or null.
_TRANSMISSION_BY_ARIA_JS = """
el => {
    const autoKw = ['automático', 'automática', 'automatic', 'automatique', 'automatik'];
    const manKw  = ['manual', 'manuale'];
    for (const d of el.querySelectorAll('[aria-label], [title]')) {
        const label = (d.getAttribute('aria-label') || d.getAttribute('title') || '').toLowerCase();
        if (autoKw.some(kw => label.includes(kw))) return 'A';
        if (manKw.some(kw => label.includes(kw))) return 'M';
    }
    return null;
}
"""


# ── Card marking ──────────────────────────────────────────────────────────────

async def _mark_valid_cards(session: BrowserSession, card_sel: str) -> dict:
    """
    Run deduplication in-browser: mark complete leaf cards with data-scraper-card.
    Returns {total, afterDedup, valid}.
    """
    return await session.page.evaluate(_MARK_CARDS_JS, [card_sel, _PRICE_RE_STR])


# ── Price cascade ─────────────────────────────────────────────────────────────

async def _extract_price_cascade(
    card,
    price_fs: FieldSelector | None,
) -> dict:
    """
    Generic price extraction with three-level cascade:
    1. Try the field_selector for price_final (if provided).
    2. JS: any descendant aria-label containing a price.
    3. JS: text nodes separated by struck/non-struck; non-struck = final.

    Returns dict with keys: price_final, currency.
    """
    empty = {"price_final": None, "currency": None}

    # Level 1: use the field_selector if provided
    if price_fs is not None:
        raw = await _apply_extraction(card, price_fs)
        if raw:
            val, cur = parse_price(raw)
            if val is not None:
                return {"price_final": val, "currency": cur}

    # Levels 2 & 3: JS cascade
    try:
        result = await card.evaluate(_PRICE_CASCADE_JS, _PRICE_RE_STR)
    except Exception:
        result = None

    if not result:
        return empty

    source = result.get("source")

    if source == "aria_label":
        text = result["text"]
        val, cur = parse_price(text)
        return {"price_final": val, "currency": cur}

    if source == "text":
        non_struck = result.get("nonStruck", [])
        struck = result.get("struck", [])

        ns_parsed = [(v, c) for t in non_struck for v, c in [parse_price(t)] if v is not None]
        s_parsed = [(v, c) for t in struck for v, c in [parse_price(t)] if v is not None]

        if ns_parsed:
            final_val = min(v for v, _ in ns_parsed)
            currency = ns_parsed[0][1]
        elif s_parsed:
            final_val = min(v for v, _ in s_parsed)
            currency = s_parsed[0][1]
        else:
            return empty

        if not currency and s_parsed:
            currency = s_parsed[0][1]

        return {"price_final": final_val, "currency": currency}

    return empty


# ── Semantic field extractors ─────────────────────────────────────────────────

async def _extract_seats_by_aria(card) -> int | None:
    """Extract seat count from aria-label/title semantics. No positional fallback."""
    try:
        result = await card.evaluate(_SEATS_BY_ARIA_JS)
        return int(result) if result is not None else None
    except Exception:
        return None


async def _extract_transmission_by_aria(
    card,
    transmission_fs: FieldSelector | None,
) -> str | None:
    """
    Extract transmission type: aria-label/title semantics first ('A'/'M'),
    then fall back to field_selector text if no aria match found.
    """
    try:
        result = await card.evaluate(_TRANSMISSION_BY_ARIA_JS)
        if result is not None:
            return result
    except Exception:
        pass
    if transmission_fs is not None:
        raw = await _apply_extraction(card, transmission_fs)
        return _coerce("transmission", raw)
    return None


# ── Field extraction helpers ──────────────────────────────────────────────────

async def _apply_extraction(card, field_sel: FieldSelector) -> str | None:
    """Apply a FieldSelector to a Playwright card locator; return raw string or None."""
    try:
        el = card.locator(field_sel.selector).first
        ext = field_sel.extraction

        if ext == "text":
            raw = await el.text_content(timeout=2_000)
            return raw.strip() if raw else None

        if ext.startswith("attribute:"):
            attr = ext.split(":", 1)[1]
            return await el.get_attribute(attr, timeout=2_000)

        if ext.startswith("regex:"):
            pattern = ext.split(":", 1)[1]
            raw = await el.text_content(timeout=2_000) or ""
            m = re.search(pattern, raw.strip())
            return m.group(1) if m else None

        return None
    except Exception:
        return None


def _coerce(field: str, raw: str | None) -> "str | int | None":
    """Type-coerce raw string for non-price fields."""
    if raw is None:
        return None
    raw = str(raw).strip()
    if not raw:
        return None

    if field in _INT_FIELDS:
        m = re.search(r"(\d+)", raw)
        return int(m.group(1)) if m else None

    return raw or None


# ── Main extractor ────────────────────────────────────────────────────────────

async def extract_vehicles_dom(
    session: BrowserSession,
    structure: ResultsStructure,
    logger: SessionLogger,
) -> list[VehicleResult]:
    """
    Apply *structure* selectors deterministically (no LLM):
    1. Mark valid leaf cards (dedup nested elements, require price presence).
    2. For each card:
       - Apply field selectors for model, group_code, availability_note.
       - Extract seats via aria-label keyword match (None if no match).
       - Extract transmission via aria-label keyword match, fallback to selector.
       - Use the three-level price cascade for price_final/currency.
    3. Return one VehicleResult per valid card.
    """
    card_sel = structure.vehicle_card_selector
    if not card_sel:
        logger.log("dom_extractor_no_selector")
        return []

    # Dedup and mark valid cards in-browser
    stats = await _mark_valid_cards(session, card_sel)
    logger.log(
        "dom_extractor_dedup",
        selector=card_sel,
        total=stats.get("total", 0),
        after_dedup=stats.get("afterDedup", 0),
        valid=stats.get("valid", 0),
    )

    cards = await session.page.locator("[data-scraper-card]").all()
    if not cards:
        logger.log("dom_extractor_no_cards_after_dedup", selector=card_sel)
        return []

    # Fields handled by dedicated extractors — skip from field_selector loop
    _SPECIAL_FIELDS = _PRICE_FIELDS | {"currency", "seats", "transmission"}
    other_selectors = [
        fs for fs in structure.field_selectors
        if fs.field not in _SPECIAL_FIELDS
    ]
    price_fs = next(
        (fs for fs in structure.field_selectors if fs.field == "price_final"),
        None,
    )
    transmission_fs = next(
        (fs for fs in structure.field_selectors if fs.field == "transmission"),
        None,
    )

    vehicles: list[VehicleResult] = []

    for card in cards:
        field_values: dict = {}

        # Generic non-special fields (model, group_code, availability_note, …)
        for fs in other_selectors:
            raw = await _apply_extraction(card, fs)
            field_values[fs.field] = _coerce(fs.field, raw)

        # Seats: semantic aria-label match only; None if no clear label found
        field_values["seats"] = await _extract_seats_by_aria(card)

        # Transmission: aria-label first, field_selector text fallback
        field_values["transmission"] = await _extract_transmission_by_aria(
            card, transmission_fs
        )

        # Price extraction via cascade
        price_data = await _extract_price_cascade(card, price_fs)
        field_values.update(price_data)

        # Build VehicleResult
        v = VehicleResult()
        for field, val in field_values.items():
            if hasattr(v, field):
                setattr(v, field, val)
        vehicles.append(v)

    (logger.log_dir / "dom_snapshots" / "vehicles_dom.json").write_text(
        json.dumps(
            [v.__dict__ for v in vehicles],
            ensure_ascii=False, indent=2, default=str,
        ),
        encoding="utf-8",
    )
    logger.log("dom_extraction", count=len(vehicles))
    return vehicles
