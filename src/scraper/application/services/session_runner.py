from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Callable, List, Optional

from ...domain.interfaces.scraper_factory import IScraperFactory
from ....shared.domain.models.result import BookingResult
from ..models.search_request import SearchRequest


async def run_session(
    factory: IScraperFactory,
    requests: List[SearchRequest],
    should_stop: Optional[Callable[[BookingResult], bool]] = None,
) -> List[Optional[BookingResult]]:
    """
    Groups requests by provider and runs them:
      - In parallel across providers.
      - Sequentially within each provider (respects rate limits).

    Returns results in the same order as the input requests.
    Positions for requests that were not executed (due to early stop) remain None.
    Used by SmartScraperOrchestrator.
    """
    if not requests:
        return []

    groups: dict[str, list[tuple[int, SearchRequest]]] = defaultdict(list)
    for i, req in enumerate(requests):
        groups[req.search.provider_name].append((i, req))

    results: list[Optional[BookingResult]] = [None] * len(requests)

    async def run_provider(indexed_requests: list) -> None:
        provider_name = indexed_requests[0][1].search.provider_name
        scraper = factory.create(provider_name)
        reqs = [req for _, req in indexed_requests]
        session_results = await scraper.scrape_session(reqs, should_stop=should_stop)
        for (i, req), result in zip(indexed_requests, session_results):
            results[i] = req.rate_filter.apply(result) if req.rate_filter else result

    await asyncio.gather(*[run_provider(reqs) for reqs in groups.values()])
    return results
