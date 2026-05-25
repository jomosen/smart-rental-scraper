"""Vía 2 (step 2): apply discovered selectors deterministically via Playwright."""
from __future__ import annotations

import json
import re

from browser_session import BrowserSession
from extraction.models import FieldSelector, ResultsStructure, VehicleResult
from session_logger import SessionLogger

# Fields that should be coerced to float (parsed as prices)
_PRICE_FIELDS = frozenset({"price_final", "price_original"})
# Fields that should be coerced to int
_INT_FIELDS = frozenset({"seats", "doors", "bags"})


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


# ── Field extraction helpers ──────────────────────────────────────────────────

async def _apply_extraction(card, field_sel: FieldSelector) -> str | None:
    """
    Apply a FieldSelector to a Playwright card locator.
    Returns the raw string (or None on any error).
    """
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


def _coerce(field: str, raw: str | None) -> "str | int | float | None":
    """Convert raw string to the appropriate Python type for the given field name."""
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None

    if field in _PRICE_FIELDS:
        val, _ = parse_price(raw)
        return val

    if field == "discount_pct":
        m = re.search(r"(\d+(?:[.,]\d+)?)", raw)
        if m:
            return float(m.group(1).replace(",", "."))
        return None

    if field in _INT_FIELDS:
        m = re.search(r"(\d+)", raw)
        return int(m.group(1)) if m else None

    return raw or None


def _build_vehicle(field_values: dict) -> VehicleResult:
    v = VehicleResult()
    for field, raw in field_values.items():
        coerced = _coerce(field, raw)
        if hasattr(v, field):
            setattr(v, field, coerced)
    return v


# ── Main extractor ────────────────────────────────────────────────────────────

async def extract_vehicles_dom(
    session: BrowserSession,
    structure: ResultsStructure,
    logger: SessionLogger,
) -> list[VehicleResult]:
    """
    Apply *structure* selectors deterministically (no LLM):
    1. Locate all vehicle cards with vehicle_card_selector.
    2. For each card, apply each FieldSelector (text / attribute / regex).
    3. Coerce types and build VehicleResult objects.

    If vehicle_card_selector returns ≤1 card (may point at the container),
    tries selector + " > *" (direct children) as a fallback.
    """
    card_sel = structure.vehicle_card_selector

    async def _count(sel: str) -> int:
        try:
            return await session.page.locator(sel).count()
        except Exception:
            return 0

    count = await _count(card_sel)
    if count <= 1:
        fallback = card_sel + " > *"
        fb_count = await _count(fallback)
        if fb_count > count:
            logger.log(
                "dom_extractor_fallback",
                original_selector=card_sel,
                fallback_selector=fallback,
                original_count=count,
                fallback_count=fb_count,
            )
            card_sel = fallback
            count = fb_count

    logger.log("dom_extractor_cards_found", selector=card_sel, count=count)

    vehicles: list[VehicleResult] = []
    cards = await session.page.locator(card_sel).all()

    for i, card in enumerate(cards):
        field_values: dict = {}
        currency_seen: str | None = None

        for fs in structure.field_selectors:
            raw = await _apply_extraction(card, fs)
            field_values[fs.field] = raw

            if fs.field in _PRICE_FIELDS and raw:
                _, cur = parse_price(raw)
                if cur:
                    currency_seen = cur

        # If currency wasn't an explicit field, fill from price parsing
        if "currency" not in field_values and currency_seen:
            field_values["currency"] = currency_seen

        vehicle = _build_vehicle(field_values)
        vehicles.append(vehicle)

    (logger.log_dir / "dom_snapshots" / "vehicles_dom.json").write_text(
        json.dumps(
            [v.__dict__ for v in vehicles],
            ensure_ascii=False, indent=2, default=str,
        ),
        encoding="utf-8",
    )
    logger.log("dom_extraction", count=len(vehicles), selector=card_sel)
    return vehicles
