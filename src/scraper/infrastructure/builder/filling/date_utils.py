"""Shared date formatting and parsing utilities."""
from __future__ import annotations

from datetime import date, datetime

_FALLBACK_FORMATS = (
    "DD/MM/YYYY",
    "YYYY-MM-DD",
    "MM/DD/YYYY",
    "DD-MM-YYYY",
    "MM-DD-YYYY",
)


def _declarative_to_strptime(fmt: str) -> str:
    result = fmt
    result = result.replace("YYYY", "%Y")
    result = result.replace("YY", "%y")
    result = result.replace("MM", "%m")
    result = result.replace("DD", "%d")
    return result


def format_date(d: date, fmt: str | None) -> str:
    """Format *d* using a declarative format string such as 'DD/MM/YYYY'.
    Supported tokens: DD, MM, YYYY, YY. Defaults to 'DD/MM/YYYY'.
    """
    fmt = fmt or "DD/MM/YYYY"
    result = fmt
    result = result.replace("YYYY", f"{d.year:04d}")
    result = result.replace("YY", f"{d.year % 100:02d}")
    result = result.replace("MM", f"{d.month:02d}")
    result = result.replace("DD", f"{d.day:02d}")
    return result


def parse_date(value: str, fmt: str | None) -> date | None:
    """Parse a date string back to a date object.

    If *fmt* is given (declarative, e.g. 'DD/MM/YYYY'), parse strictly by that
    format. Otherwise try common formats in order.
    Returns None if parsing fails.
    """
    value = value.strip()
    if not value:
        return None
    candidates: tuple[str, ...] = (fmt,) if fmt is not None else _FALLBACK_FORMATS
    for candidate in candidates:
        try:
            return datetime.strptime(value, _declarative_to_strptime(candidate)).date()
        except ValueError:
            continue
    return None
