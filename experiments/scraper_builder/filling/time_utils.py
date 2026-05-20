"""Time string normalization utilities."""
from __future__ import annotations

import re


def normalize_time(value: str) -> tuple[int, int] | None:
    """
    Extract (hour, minute) in 24h from a time string in various formats.

    Handles: "10:00", "10:00 AM", "10:00 PM", "10h", "10h00", "9.30",
             "10:00 a.m.", "22:30", and plain hour digits.
    Returns (hour, minute) or None if the string cannot be parsed as a time.

    Examples:
      "10:00"    -> (10, 0)
      "10:00 AM" -> (10, 0)
      "10:00 PM" -> (22, 0)
      "10h"      -> (10, 0)
      "9.30"     -> (9, 30)
      "22:30"    -> (22, 30)
      "Alicante" -> None
    """
    text = value.strip().lower()

    is_pm = bool(re.search(r'p\.?m\.?', text))
    is_am = bool(re.search(r'a\.?m\.?', text))

    # Remove AM/PM text before extracting digits
    clean = re.sub(r'[ap]\.?m\.?', '', text).strip()

    # HH:MM, HHhMM, HH.MM  (requires two digits after separator)
    m = re.search(r'(\d{1,2})[:h\.](\d{2})', clean)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
    else:
        # Bare hour: "10h" or "10" alone
        m2 = re.search(r'^(\d{1,2})\s*h?\s*$', clean)
        if m2:
            hour, minute = int(m2.group(1)), 0
        else:
            return None

    if is_pm and hour < 12:
        hour += 12
    if is_am and hour == 12:
        hour = 0

    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return (hour, minute)
    return None
