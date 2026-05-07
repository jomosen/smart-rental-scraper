from __future__ import annotations

import datetime
import os
from decimal import Decimal

from sqlalchemy.orm import Session

from ..models.catalog import PriceObservation
from .price_observation_heartbeat import PriceObservationHeartbeatRepository

_THRESHOLD = Decimal(os.environ.get("PRICE_CHANGE_THRESHOLD", "0.01"))


class PriceObservationRepository:
    def __init__(self, session: Session) -> None:
        self._s = session
        self._heartbeats = PriceObservationHeartbeatRepository(session)

    def insert_if_changed(
        self,
        provider_id: int,
        provider_location_id: int,
        provider_rate_id: int,
        provider_vehicle_group_id: int,
        scrape_run_id: int,
        pickup_date: datetime.date,
        duration_days: int,
        price_per_day: Decimal,
        total_price: Decimal,
        currency: str,
        observed_at: datetime.datetime,
    ) -> bool:
        """Persist a new observation only when the price changed beyond the threshold.

        Always updates the heartbeat regardless of whether a new observation
        is written. Returns True if a new observation row was inserted.
        """
        from sqlalchemy import select
        from ..models.catalog import PriceObservationHeartbeat

        heartbeat = self._s.get(
            PriceObservationHeartbeat,
            (
                provider_id,
                provider_location_id,
                provider_rate_id,
                provider_vehicle_group_id,
                pickup_date,
                duration_days,
            ),
        )

        changed = (
            heartbeat is None
            or abs(heartbeat.last_price_per_day - price_per_day) > _THRESHOLD
        )

        if changed:
            obs = PriceObservation(
                provider_id=provider_id,
                provider_location_id=provider_location_id,
                provider_rate_id=provider_rate_id,
                provider_vehicle_group_id=provider_vehicle_group_id,
                scrape_run_id=scrape_run_id,
                pickup_date=pickup_date,
                duration_days=duration_days,
                price_per_day=price_per_day,
                total_price=total_price,
                currency=currency,
                observed_at=observed_at,
            )
            self._s.add(obs)

        self._heartbeats.upsert(
            provider_id=provider_id,
            provider_location_id=provider_location_id,
            provider_rate_id=provider_rate_id,
            provider_vehicle_group_id=provider_vehicle_group_id,
            pickup_date=pickup_date,
            duration_days=duration_days,
            price_per_day=price_per_day,
            checked_at=observed_at,
        )

        return changed
