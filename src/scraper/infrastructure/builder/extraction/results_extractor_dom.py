"""Vía 2 (step 2): apply discovered selectors deterministically via Playwright."""
from __future__ import annotations

import json
import re

from ..browser_session import BrowserSession
from .models import FieldSelector, ResultsStructure, VehicleResult
from ..session_logger import SessionLogger

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
#
# Two-pass strategy:
#   Pass 1 — use the LLM-provided cardSelector + leaf + price filter.
#   Pass 2 — if Pass 1 yields < 3 valid cards, heuristic fallback:
#             walk up from each price text node to find the nearest ancestor
#             that contains a heading element (h1–h5 / role=heading), then
#             apply the same leaf + price filter. Provider-agnostic.
_MARK_CARDS_JS = """
([cardSelector, priceReStr]) => {
    const priceRe = new RegExp(priceReStr, 'i');

    document.querySelectorAll('[data-scraper-card]').forEach(
        el => el.removeAttribute('data-scraper-card'));

    function hasPrice(el) {
        if (priceRe.test(el.textContent)) return true;
        for (const d of el.querySelectorAll('[aria-label]')) {
            if (priceRe.test(d.getAttribute('aria-label') || '')) return true;
        }
        return false;
    }

    // ── Pass 1: LLM-provided selector ────────────────────────────────────
    const allCards = Array.from(document.querySelectorAll(cardSelector));
    const leafCards = allCards.filter(el =>
        !allCards.some(other => other !== el && el.contains(other)));
    let complete = leafCards.filter(hasPrice);

    // ── Pass 2: heuristic fallback when selector yields < 3 valid cards ──
    let usedHeuristic = false;
    if (complete.length < 3) {
        usedHeuristic = true;
        // Walk up from each price text node to the nearest heading-containing ancestor.
        // "Visited" set prevents adding the same card twice.
        const visited = new Set();
        const candidates = [];
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        let node;
        while ((node = walker.nextNode())) {
            if (!priceRe.test(node.textContent)) continue;
            if (!node.parentElement || node.parentElement.offsetParent === null) continue;
            let el = node.parentElement;
            while (el && el !== document.body) {
                if (visited.has(el)) break;
                if (el.querySelector('h1,h2,h3,h4,h5,[role="heading"]') !== null &&
                        hasPrice(el)) {
                    visited.add(el);
                    candidates.push(el);
                    break;
                }
                el = el.parentElement;
            }
        }
        // Leaf filter: discard any candidate that contains another candidate
        const leafFallback = candidates.filter(el =>
            !candidates.some(other => other !== el && el.contains(other)));
        if (leafFallback.length > complete.length) {
            complete = leafFallback;
        }
    }

    complete.forEach((el, i) => el.setAttribute('data-scraper-card', String(i)));
    return {
        total: allCards.length,
        afterDedup: leafCards.length,
        valid: complete.length,
        usedHeuristic: usedHeuristic,
    };
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

# Default keyword lists — used as fallback in LLM path (no recipe-provided keywords).
# recipe_writer.py imports these to embed them in generated recipes.
DEFAULT_SEAT_KEYWORDS: list[str] = [
    'plazas', 'asientos', 'seats', 'places', 'sitzplätze', 'posti', 'plaza',
]
DEFAULT_AUTO_KEYWORDS: list[str] = [
    'automático', 'automática', 'automatic', 'automatique', 'automatik',
]
DEFAULT_MANUAL_KEYWORDS: list[str] = ['manual', 'manuale']

# JS: scan descendants for aria-label/title containing seat keywords.
# Parameterized: (el, keywords) where keywords is a JSON-serialised JS array.
# Returns integer seat count or null.
_SEATS_BY_ARIA_JS = """
(el, keywords) => {
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

# JS: scan descendants for aria-label/title indicating transmission type.
# Parameterized: (el, kwPair) where kwPair = [autoKw, manKw], each a JS array.
# Returns 'A' (automatic), 'M' (manual), or null.
_TRANSMISSION_BY_ARIA_JS = """
(el, kwPair) => {
    const autoKw = kwPair[0], manKw = kwPair[1];
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

async def _extract_seats_by_aria(card, keywords: list[str]) -> int | None:
    """Extract seat count from aria-label/title semantics using the provided keyword list."""
    try:
        result = await card.evaluate(_SEATS_BY_ARIA_JS, keywords)
        return int(result) if result is not None else None
    except Exception:
        return None


async def _extract_transmission_by_aria(
    card,
    auto_keywords: list[str],
    manual_keywords: list[str],
    transmission_fs: FieldSelector | None,
) -> str | None:
    """
    Extract transmission: aria-label/title keyword match ('A'/'M') first,
    then fall back to field_selector text extraction if no aria match.
    """
    try:
        result = await card.evaluate(_TRANSMISSION_BY_ARIA_JS, [auto_keywords, manual_keywords])
        if result is not None:
            return result
    except Exception:
        pass
    if transmission_fs is not None:
        raw = await _apply_extraction(card, transmission_fs)
        return _coerce("transmission", raw)
    return None


# ── Field extraction helpers ──────────────────────────────────────────────────

async def _find_in_card(card, selector: str):
    """Locate *selector* inside the card, tolerating a self-referencing prefix.

    Field selectors are written relative to whatever element the classifier
    called "the card" ("section span b"). When the runtime card element IS
    that first compound (a <section> marked by the heuristic), a descendant
    query for "section span b" inside it matches nothing. In that case retry
    with the leading compound stripped ("span b") — same target, one level up.
    """
    loc = card.locator(selector).first
    if await loc.count() > 0:
        return loc
    parts = selector.strip().split(None, 1)
    if len(parts) == 2:
        head, rest = parts
        try:
            if await card.evaluate(
                "(el, sel) => { try { return el.matches(sel); } catch (e) { return false; } }",
                arg=head,
            ):
                return card.locator(rest).first
        except Exception:
            pass
    return loc


async def _apply_extraction(card, field_sel: FieldSelector) -> str | None:
    """Apply a FieldSelector to a Playwright card locator; return raw string or None."""
    try:
        el = await _find_in_card(card, field_sel.selector)
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
    Apply *structure* selectors deterministically (no LLM).

    Dispatches each FieldSelector by its extraction type:
      text/attribute/regex     → _apply_extraction (generic CSS path)
      aria_keyword             → _extract_seats_by_aria with recipe keywords
      aria_keyword_transmission→ _extract_transmission_by_aria with recipe keywords
      price_cascade            → _extract_price_cascade (JS cascade, no CSS level-1)

    When no semantic FieldSelector is present (LLM path), defaults fire for
    seats and transmission; the JS cascade fires for price.
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
        used_heuristic=stats.get("usedHeuristic", False),
    )

    cards = await session.page.locator("[data-scraper-card]").all()
    if not cards:
        logger.log("dom_extractor_no_cards_after_dedup", selector=card_sel)
        return []

    # Pre-classify field selectors by extraction strategy.
    #
    # Recipe path:  FSes carry extraction="aria_keyword" / "aria_keyword_transmission" /
    #               "price_cascade" — routed to the matching semantic function.
    # LLM path:     FSes carry "text" / "attribute:X" / "regex:Y" — routed to
    #               _apply_extraction.  Aria/cascade fallbacks fire with default keywords.
    seats_fs = None           # aria_keyword for seats (recipe path)
    trans_semantic_fs = None  # aria_keyword_transmission (recipe path)
    price_cascade_fs = None   # price_cascade (recipe path)
    trans_text_fs = None      # text/regex selector for transmission (LLM fallback)
    price_text_fs = None      # text/regex selector for price_final (LLM level-1)
    generic_selectors = []    # all other text/attribute/regex selectors

    for fs in structure.field_selectors:
        if fs.extraction == "aria_keyword" and fs.field == "seats":
            seats_fs = fs
        elif fs.extraction == "aria_keyword_transmission" and fs.field == "transmission":
            trans_semantic_fs = fs
        elif fs.extraction == "price_cascade" and fs.field == "price_final":
            price_cascade_fs = fs
        elif fs.field == "transmission":
            trans_text_fs = fs          # LLM-provided selector, used as aria fallback
        elif fs.field in _PRICE_FIELDS | {"currency"}:
            price_text_fs = fs          # LLM-provided price selector (level-1 cascade)
        elif fs.field != "seats":       # seats with non-semantic selector → ignored
            generic_selectors.append(fs)

    vehicles: list[VehicleResult] = []
    price_scope_missing = 0

    for card in cards:
        field_values: dict = {}

        # Generic fields (model, group_code, …) — text/attribute/regex selectors
        for fs in generic_selectors:
            raw = await _apply_extraction(card, fs)
            field_values[fs.field] = _coerce(fs.field, raw)

        # Seats: recipe-provided keywords → defaults
        seat_kw = (seats_fs.keywords or DEFAULT_SEAT_KEYWORDS) if seats_fs else DEFAULT_SEAT_KEYWORDS
        field_values["seats"] = await _extract_seats_by_aria(card, seat_kw)

        # Transmission: recipe-provided keywords → defaults; text selector as aria fallback
        if trans_semantic_fs:
            auto_kw = trans_semantic_fs.auto_keywords or DEFAULT_AUTO_KEYWORDS
            man_kw = trans_semantic_fs.manual_keywords or DEFAULT_MANUAL_KEYWORDS
            field_values["transmission"] = await _extract_transmission_by_aria(
                card, auto_kw, man_kw, None
            )
        else:
            field_values["transmission"] = await _extract_transmission_by_aria(
                card, DEFAULT_AUTO_KEYWORDS, DEFAULT_MANUAL_KEYWORDS, trans_text_fs
            )

        # Price: skip level-1 selector in recipe path (price_cascade_fs present)
        price_sel = None if price_cascade_fs else price_text_fs
        # A selector on a price_cascade FS is a SCOPE: the cascade runs inside
        # that sub-element (e.g. the block of one specific tariff on multi-rate
        # cards). If the scope is demanded but absent, the price stays None —
        # never silently fall back to the whole card, that would quietly
        # extract the wrong tariff.
        price_scope = card
        if price_cascade_fs and price_cascade_fs.selector:
            scoped = card.locator(price_cascade_fs.selector).first
            price_scope = scoped if await scoped.count() else None
        if price_scope is None:
            price_scope_missing += 1
            price_data = {"price_final": None, "currency": None}
        else:
            price_data = await _extract_price_cascade(price_scope, price_sel)
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
    if price_scope_missing:
        logger.log("price_scope_missing", count=price_scope_missing)
    logger.log("dom_extraction", count=len(vehicles))
    return vehicles
