"""Data transfer objects for PriceQueryService outputs."""
from __future__ import annotations

from dataclasses import dataclass
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
    coverage_by_duration: Optional[dict[int, int]] = None
    # None for single-provider queries (coverage does not apply).
    # dict[duration → count of subscriptions contributing data] for
    # market_average and market_minimum.


@dataclass
class FormatATable:
    """Format A price table returned by all three PriceQueryService methods."""
    rows: list[FormatARow]
    metadata: dict
