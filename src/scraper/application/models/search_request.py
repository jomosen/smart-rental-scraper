from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from ....shared.domain.models.search import BookingSearch
from ..filters.rate_filter import RateFilter


@dataclass(frozen=True)
class SearchRequest:
    """Groups a search with its rate filter and optional fallback searches."""

    search: BookingSearch
    rate_filter: Optional[RateFilter] = None
    fallback_searches: Tuple[BookingSearch, ...] = ()
