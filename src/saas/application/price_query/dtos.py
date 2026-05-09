"""Data transfer objects for PriceQueryService outputs."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional


@dataclass
class ZoneRange:
    """Inclusive [start_date, end_date] for a homogeneous zone."""
    start_date: date
    end_date: date


@dataclass
class FormatARow:
    """One row in a Format A price table: one client group × one date range."""
    client_group_code: str
    period_start: date
    period_end: date                              # inclusive
    prices_by_duration: dict[int, Optional[Decimal]]  # None when no observation
    coverage: Optional[int] = None               # None in per-provider tariff; int in aggregates


@dataclass
class FormatATable:
    """Format A price table returned by all three PriceQueryService methods."""
    rows: list[FormatARow]
    metadata: dict
