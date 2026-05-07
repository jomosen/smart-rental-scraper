from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import List

from ....shared.domain.models.search import BookingSearch, Location
from ....shared.domain.models.season import HomogeneousZone
from ..models.booking_provider import BookingProvider
from ..models.season_internals import PricePoint, SeasonBoundary


class ISeasonProbe(ABC):
    """
    SRP: builds probe searches with long segments.
    Does not interpret prices or make decisions about zones.
    """

    @abstractmethod
    def build_probe_searches(
        self,
        provider: BookingProvider,
        pickup_location: Location,
        dropoff_location: Location,
        period_start: date,
        period_end: date,
        pickup_hour: int = 10,
    ) -> List[BookingSearch]:
        """
        Returns probe searches with 7-day rentals spaced weekly
        to cover the period and detect season changes.
        Invariant: never generates dates where dropoff > period_end.
        """
        ...


class ISeasonAnalyzer(ABC):
    """
    SRP: receives already-obtained PricePoints and detects homogeneous zones.
    Knows nothing about scrapers or how prices were obtained.
    """

    @abstractmethod
    def detect_zones(
        self,
        price_points: List[PricePoint],
        period_start: date,
        period_end: date,
        car_group: str,
    ) -> List[HomogeneousZone]:
        """
        Analyses price points and returns the homogeneous zones for the period.
        A zone is homogeneous if the price does not vary beyond the configured threshold.
        """
        ...


class ISearchPlanBuilder(ABC):
    """
    SRP: generates short-segment searches from detected zones.
    OCP: new duration selection strategies are added by implementing this interface.
    """

    @abstractmethod
    def build_short_searches(
        self,
        zones: List[HomogeneousZone],
        provider: BookingProvider,
        pickup_location: Location,
        dropoff_location: Location,
        durations: List[int],
        period_end: date,
        pickup_hour: int = 10,
    ) -> List[BookingSearch]:
        """
        For each zone generates one search per duration using
        zone.representative_date as pickup_date.
        Only includes combinations whose return date does not exceed period_end.
        """
        ...


class ISeasonBoundaryRepository(ABC):
    """
    SRP: persists and retrieves detected season boundaries.
    Decoupled from the algorithm: the orchestrator does not know whether it is stored in JSON, DB, etc.
    """

    @abstractmethod
    async def save(
        self,
        provider: str,
        car_group: str,
        boundaries: List[SeasonBoundary],
        zones: List[HomogeneousZone],
        period_start: date,
        period_end: date,
    ) -> None:
        ...
